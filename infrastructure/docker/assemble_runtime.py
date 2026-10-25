"""Assemble a Linux runtime from a locked, trusted build image.

Only installed builder code is inspected with ldd, never uploaded/user binaries.
Retain the complete venv, CPython, license notices and unmodified package metadata.
The manifest distinguishes copied files from the builder's package inventory.
Runtime imports/smoke tests and vulnerability review remain separate CI gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections import deque
from pathlib import Path, PurePosixPath

TREES = (
    "/app",
    "/usr/local/lib/python3.12",
    "/etc/ssl/certs",
    "/usr/share/ca-certificates",
    "/usr/share/common-licenses",
    "/usr/share/zoneinfo",
    "/usr/lib/locale",
    "/var/lib/dpkg",
)
REQUIRED_FILES = (
    "/usr/local/bin/python",
    "/usr/local/bin/python3",
    "/usr/local/bin/python3.12",
    "/etc/os-release",
    "/etc/nsswitch.conf",
    "/etc/ssl/openssl.cnf",
    "/etc/ssl/certs/ca-certificates.crt",
)
ALLOWED_TREES = TREES + ("/usr/lib", "/usr/lib64", "/lib", "/lib64", "/usr/share/doc")
OPTIONAL_FILES = (
    "/etc/gai.conf",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/localtime",
    "/etc/timezone",
    "/usr/lib/ssl/cert.pem",
    "/usr/lib/ssl/certs",
    "/usr/lib/ssl/openssl.cnf",
)
FORBIDDEN_TOOLS = {"mount", "nsenter", "infocmp", "uv", "uvx", "pip", "pip3", "pip3.12"}


class AssemblyError(RuntimeError):
    pass


def dependencies(path: Path) -> list[str]:
    """Resolve the builder's native closure; ldd is confined to trusted build inputs."""
    process = subprocess.run(
        ["/usr/bin/ldd", str(path)],
        capture_output=True,
        text=True,
        timeout=20,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    )
    lines = process.stdout.splitlines()
    if "not found" in process.stdout:
        raise AssemblyError("native_dependency_missing")
    static = any(
        line.strip() in {"statically linked", "not a dynamic executable"} for line in lines
    )
    if process.returncode and not static:
        raise AssemblyError("native_dependency_inspection_failed")
    found = []
    for line in lines:
        value = line.strip()
        if not value or value in {"statically linked", "not a dynamic executable"}:
            continue
        if value.startswith(("linux-vdso.", "linux-gate.")):
            continue
        if "=>" in value:
            value = value.partition("=>")[2].strip()
        value = value.partition(" (")[0].strip()
        if not value.startswith("/") or any(char.isspace() for char in value):
            raise AssemblyError("native_dependency_output_unrecognized")
        found.append(value)
    if not found and not static:
        raise AssemblyError("native_dependency_evidence_empty")
    return sorted(set(found))


class RuntimeAssembler:
    def __init__(self, source: Path, output: Path, *, resolver=dependencies):
        self.source = source.resolve(strict=True)
        self.output = output.resolve()
        if (
            self.output == self.source
            or self.output.exists()
            or any(
                self.output.is_relative_to(self.source / root.lstrip("/"))
                for root in ALLOWED_TREES + ("/usr/local/bin",)
            )
        ):
            raise AssemblyError("runtime_output_must_be_new")
        self.output.mkdir(mode=0o755)
        self.resolver = resolver
        self.files: dict[str, dict] = {}
        self.visited: set[str] = set()
        self.native: deque[str] = deque()
        self.native_closure: dict[str, list[str]] = {}
        self.total_bytes = 0

    @staticmethod
    def allowed(logical: str) -> bool:
        return (
            logical in REQUIRED_FILES + OPTIONAL_FILES
            or logical in {"/usr/local/lib/libpython3.12.so", "/usr/local/lib/libpython3.12.so.1.0"}
            or any(logical == root or logical.startswith(root + "/") for root in ALLOWED_TREES)
        )

    def resolve(self, logical: str) -> tuple[str, list[tuple[str, str]]]:
        if not logical.startswith("/"):
            raise AssemblyError("runtime_path_must_be_absolute")
        parts: list[str] = []
        pending = deque(PurePosixPath(logical).parts[1:])
        links: list[tuple[str, str]] = []
        while pending:
            part = pending.popleft()
            if part in {"", "."}:
                continue
            if part == "..":
                if not parts:
                    raise AssemblyError("runtime_symlink_root_escape")
                parts.pop()
                continue
            candidate = self.source.joinpath(*parts, part)
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode):
                if len(links) >= 32:
                    raise AssemblyError("runtime_symlink_limit")
                target = os.readlink(candidate)
                alias = "/" + "/".join([*parts, part])
                links.append((alias, target))
                if target.startswith("/"):
                    parts = []
                pending.extendleft(reversed(PurePosixPath(target).parts))
                if pending and pending[0] == "/":
                    pending.popleft()
            else:
                parts.append(part)
        return "/" + "/".join(parts), links

    def alias(self, logical: str, original: str) -> None:
        if not self.allowed(logical):
            raise AssemblyError("runtime_symlink_not_allowed")
        self.check_payload(logical, stat.S_IFLNK | 0o777)
        target = (
            original if original.startswith("/") else str(PurePosixPath(logical).parent / original)
        )
        resolved, _ = self.resolve(target)
        if not self.allowed(resolved):
            raise AssemblyError("runtime_symlink_target_not_allowed")
        relative = os.path.relpath(resolved, str(PurePosixPath(logical).parent))
        destination = self.output / logical.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            if os.readlink(destination) != relative:
                raise AssemblyError("runtime_symlink_conflict")
        elif destination.exists():
            raise AssemblyError("runtime_symlink_conflict")
        else:
            destination.symlink_to(relative)
        self.files[logical] = {"kind": "symlink", "builder_target": original, "target": relative}

    @staticmethod
    def check_payload(logical: str, mode: int) -> None:
        path = PurePosixPath(logical)
        if mode & (stat.S_ISUID | stat.S_ISGID):
            raise AssemblyError("runtime_suid_or_sgid_forbidden")
        if (
            (
                str(path.parent) in {"/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin"}
                and path.name in FORBIDDEN_TOOLS
            )
            or path.name.startswith(("libacl.so", "libmount.so"))
            or path.name in {"systemd-homed", "systemd-homework"}
            or path.name.startswith(".env")
            or logical.startswith("/usr/local/lib/python3.12/ensurepip")
            or re.match(r"^/usr/local/lib/python3\.12/site-packages/(pip|uv)(/|-|$)", logical)
        ):
            raise AssemblyError("runtime_forbidden_payload")

    def copy(self, logical: str) -> None:
        resolved, links = self.resolve(logical)
        if not self.allowed(resolved):
            raise AssemblyError("runtime_source_not_allowed")
        for alias, target in links:
            self.alias(alias, target)
        if resolved in self.visited:
            return
        self.visited.add(resolved)
        if len(self.visited) > 100000:
            raise AssemblyError("runtime_entry_limit")
        source = self.source / resolved.lstrip("/")
        destination = self.output / resolved.lstrip("/")
        before = source.lstat()
        self.check_payload(resolved, before.st_mode)
        if stat.S_ISDIR(before.st_mode):
            destination.mkdir(parents=True, exist_ok=True)
            for child in sorted(source.iterdir()):
                self.copy(str(PurePosixPath(resolved) / child.name))
            return
        if not stat.S_ISREG(before.st_mode):
            raise AssemblyError("runtime_special_file_forbidden")
        self.total_bytes += before.st_size
        if self.total_bytes > 3_000_000_000:
            raise AssemblyError("runtime_copy_byte_limit")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(stat.S_IMODE(before.st_mode))
        if (destination.stat().st_uid, destination.stat().st_gid) != (before.st_uid, before.st_gid):
            os.chown(destination, before.st_uid, before.st_gid)
        with source.open("rb") as file:
            magic = file.read(4)
            file.seek(0)
            original_digest = hashlib.file_digest(file, "sha256").hexdigest()
        with destination.open("rb") as file:
            copied_digest = hashlib.file_digest(file, "sha256").hexdigest()
        after = source.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ) or copied_digest != original_digest:
            raise AssemblyError("runtime_source_changed_during_copy")
        self.files[resolved] = {
            "kind": "file",
            "sha256": copied_digest,
            "size": before.st_size,
            "mode": oct(stat.S_IMODE(before.st_mode)),
            "uid": before.st_uid,
            "gid": before.st_gid,
        }
        if magic == b"\x7fELF":
            self.native.append(resolved)

    def close_native_dependencies(self) -> None:
        while self.native:
            logical = self.native.popleft()
            if logical in self.native_closure:
                continue
            found = self.resolver(self.source / logical.lstrip("/"))
            self.native_closure[logical] = found
            for dependency in found:
                self.copy(dependency)

    def finish(self, *, role: str) -> dict:
        self.close_native_dependencies()
        etc = self.output / "etc"
        etc.mkdir(exist_ok=True)
        (etc / "passwd").write_text(
            "root:x:0:0:root:/nonexistent:/nonexistent\npenforge:x:65532:65532:Penforge:/nonexistent:/nonexistent\n"
        )
        (etc / "group").write_text("root:x:0:\npenforge:x:65532:\n")
        temporary = self.output / "tmp"
        temporary.mkdir(exist_ok=True)
        temporary.chmod(0o1777)
        result = {
            "schema_version": 1,
            "role": role,
            "status": "assembled_awaiting_linux_smoke_and_scan",
            "copied_bytes": self.total_bytes,
            "copied_files": self.files,
            "native_dependencies": self.native_closure,
            "package_metadata": "Unmodified builder dpkg metadata; copied_files records the actual runtime subset. Python distribution metadata/licenses are retained with the complete venv.",
            "security_disposition": "none",
        }
        evidence = self.output / "usr/share/penforge-runtime"
        evidence.mkdir(parents=True)
        (evidence / "manifest.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--role", choices=("api", "worker"), required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Runtime assembly requires the Linux build image")
    assembler = RuntimeAssembler(Path("/"), args.output)
    for logical in TREES + REQUIRED_FILES:
        assembler.copy(logical)
    for logical in OPTIONAL_FILES:
        if Path(logical).exists() or Path(logical).is_symlink():
            assembler.copy(logical)
    # Preserve redistribution notices without inheriting documentation executables.
    for notice in sorted(Path("/usr/share/doc").glob("*/copyright")):
        assembler.copy(str(notice))
    # Explicit dlopen/NSS/crypto data: ELF tables alone do not establish runtime closure.
    for pattern in (
        "/usr/lib/*-linux-*/libnss_*.so*",
        "/usr/lib/*-linux-*/libresolv.so*",
        "/usr/lib/*-linux-*/libfreetype.so*",
        "/usr/lib/*-linux-*/libharfbuzz.so*",
        "/usr/lib/*-linux-*/gconv",
        "/usr/lib/*-linux-*/ossl-modules",
    ):
        for path in sorted(Path("/").glob(pattern.lstrip("/"))):
            assembler.copy(str(path))
    result = assembler.finish(role=args.role)
    print(
        json.dumps(
            {
                "status": result["status"],
                "copied_files": len(result["copied_files"]),
                "copied_bytes": result["copied_bytes"],
            }
        )
    )


if __name__ == "__main__":
    main()
