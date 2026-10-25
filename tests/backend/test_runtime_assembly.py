"""Builder-only filesystem controls, without executing native or model code."""

import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from infrastructure.docker.assemble_runtime import AssemblyError, RuntimeAssembler, dependencies


def put(root, name, data=b"fixture", mode=0o644):
    path = root / name.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    return path


@pytest.fixture
def roots(tmp_path):
    source, output = tmp_path / "source", tmp_path / "runtime"
    source.mkdir()
    return source, output


def test_transitive_native_closure_preserves_bytes_licenses_and_package_metadata(roots):
    source, output = roots
    main = "/app/.venv/bin/parser"
    first, second = "/usr/lib/libfirst.so", "/usr/lib/libsecond.so"
    graph = {main: [first], first: [second], second: [first]}
    for name in graph:
        put(source, name, b"\x7fELF" + name.encode(), mode=0o755)
    put(source, "/app/.venv/lib/python3.12/site-packages/pip/__init__.py")
    put(source, "/var/lib/dpkg/status", b"unmodified builder metadata")
    put(source, "/usr/share/common-licenses/MIT", b"license terms")
    calls = []

    def resolve(path):
        name = "/" + str(path.relative_to(source))
        calls.append(name)
        return graph[name]

    assembler = RuntimeAssembler(source, output, resolver=resolve)
    for name in ("/app", "/var/lib/dpkg", "/usr/share/common-licenses"):
        assembler.copy(name)
    result = assembler.finish(role="worker")
    assert set(calls) == set(graph) and len(calls) == 3
    for name, record in result["copied_files"].items():
        actual = output / name.lstrip("/")
        assert actual.read_bytes() == (source / name.lstrip("/")).read_bytes()
        assert hashlib.sha256(actual.read_bytes()).hexdigest() == record["sha256"]
    assert (output / "tmp").stat().st_mode & 0o7777 == 0o1777
    assert "65532" in (output / "etc/passwd").read_text()
    assert result["security_disposition"] == "none"
    assert json.loads((output / "usr/share/penforge-runtime/manifest.json").read_text()) == result


def test_absolute_and_relative_symlinks_stay_within_the_copied_root(roots):
    source, output = roots
    put(source, "/usr/local/bin/python3.12")
    (source / "usr/local/bin/python3").symlink_to("python3.12")
    (source / "usr/local/bin/python").symlink_to("/usr/local/bin/python3")
    assembler = RuntimeAssembler(source, output)
    assembler.copy("/usr/local/bin/python")
    assert (output / "usr/local/bin/python").read_bytes() == b"fixture"
    assert (output / "usr/local/bin/python").resolve().is_relative_to(output)
    assert not (output / "usr/local/bin/python").readlink().is_absolute()


@pytest.mark.parametrize(
    "logical",
    [
        "/usr/bin/mount",
        "/usr/bin/infocmp",
        "/usr/bin/nsenter",
        "/usr/local/bin/uv",
        "/usr/lib/libacl.so.1",
        "/usr/lib/libmount.so.1",
        "/app/.env.local",
        "/usr/local/lib/python3.12/site-packages/pip/__init__.py",
        "/usr/local/lib/python3.12/ensurepip/__init__.py",
    ],
)
def test_forbidden_payload_cannot_enter_runtime(roots, logical):
    source, output = roots
    put(source, logical)
    assembler = RuntimeAssembler(source, output)
    with pytest.raises(AssemblyError):
        assembler.copy(logical)


def test_forbidden_library_symlink_cannot_disguise_allowed_target(roots):
    source, output = roots
    put(source, "/usr/lib/libsafe.so")
    (source / "usr/lib/libmount.so.1").symlink_to("libsafe.so")
    assembler = RuntimeAssembler(source, output)
    with pytest.raises(AssemblyError, match="forbidden_payload"):
        assembler.copy("/usr/lib/libmount.so.1")


@pytest.mark.parametrize("mode", [stat.S_ISUID | 0o755, stat.S_ISGID | 0o755])
def test_privileged_modes_refuse(roots, mode, monkeypatch):
    source, output = roots
    privileged = put(source, "/app/privileged")
    original = Path.lstat

    def lstat(path):
        result = original(path)
        # The macOS agent sandbox strips privileged chmod bits; model Linux's
        # actual lstat result without changing the production policy.
        return os.stat_result((stat.S_IFREG | mode, *result[1:])) if path == privileged else result

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(AssemblyError, match="suid_or_sgid"):
        RuntimeAssembler(source, output).copy("/app")


def test_missing_dependency_refuses_instead_of_omitting_it(roots):
    source, output = roots
    put(source, "/app/parser", b"\x7fELF")
    assembler = RuntimeAssembler(source, output, resolver=lambda _: ["/usr/lib/missing.so"])
    assembler.copy("/app")
    with pytest.raises(FileNotFoundError):
        assembler.finish(role="worker")


@pytest.mark.parametrize("target", ["../../../../etc/shadow", "/etc/shadow"])
def test_unsafe_symlink_is_not_copied(roots, target):
    source, output = roots
    put(source, "/app/data")
    put(source, "/etc/shadow", b"never copy credentials")
    (source / "app/link").symlink_to(target)
    with pytest.raises(AssemblyError):
        RuntimeAssembler(source, output).copy("/app")
    assert not (output / "etc/shadow").exists()


def test_existing_output_is_never_overwritten(roots):
    source, output = roots
    output.mkdir()
    with pytest.raises(AssemblyError, match="must_be_new"):
        RuntimeAssembler(source, output)


def test_output_cannot_be_inside_a_copied_tree(roots):
    source, _ = roots
    with pytest.raises(AssemblyError, match="must_be_new"):
        RuntimeAssembler(source, source / "app/runtime")


def test_loader_configuration_directory_preserves_children_without_widening_etc(roots):
    source, output = roots
    config = "/etc/ld.so.conf.d/x86_64-linux-gnu.conf"
    put(source, config, b"/usr/local/lib\n/usr/lib/x86_64-linux-gnu\n")
    put(source, "/etc/shadow", b"not part of runtime")
    assembler = RuntimeAssembler(source, output)
    assembler.copy("/etc/ld.so.conf.d")
    assert (output / config.lstrip("/")).read_bytes() == (source / config.lstrip("/")).read_bytes()
    with pytest.raises(AssemblyError, match="runtime_source_not_allowed:/etc/shadow"):
        assembler.copy("/etc/shadow")
    assert not (output / "etc/shadow").exists()


@pytest.mark.parametrize(
    "stdout,returncode,expected",
    [
        (
            "linux-vdso.so.1 (0x1)\nlibc.so.6 => /lib/libc.so.6 (0x2)\n/lib64/ld.so (0x3)\n",
            0,
            ["/lib/libc.so.6", "/lib64/ld.so"],
        ),
        ("statically linked\n", 0, []),
        ("not a dynamic executable\n", 1, []),
    ],
)
def test_native_resolver_parses_loader_and_library_paths(monkeypatch, stdout, returncode, expected):
    def run(command, **kwargs):
        assert command == ["/usr/bin/ldd", "/trusted/extension.so"]
        assert set(kwargs["env"]) == {"PATH", "LC_ALL"}
        return SimpleNamespace(stdout=stdout, returncode=returncode)

    monkeypatch.setattr("infrastructure.docker.assemble_runtime.subprocess.run", run)
    assert dependencies(Path("/trusted/extension.so")) == expected


@pytest.mark.parametrize(
    "stdout,returncode",
    [("libbad => not found\n", 0), ("", 0), ("", 1), ("unexpected output\n", 0)],
)
def test_native_inspection_fails_closed(monkeypatch, stdout, returncode):
    monkeypatch.setattr(
        "infrastructure.docker.assemble_runtime.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=stdout, returncode=returncode),
    )
    with pytest.raises(AssemblyError):
        dependencies(Path("/trusted/extension.so"))
