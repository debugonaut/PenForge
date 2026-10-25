"""Positive, negative and bounded controls for read-only image component evidence."""

import builtins
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.image_security_inventory import Limits, inventory, no_global_installers

ROOT = Path(__file__).resolve().parents[2]
STATUS = b"Package: base-files\nStatus: install ok installed\nVersion: 13.8\nArchitecture: amd64\n"


def put(root, path, data=b"fixture component"):
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


@pytest.fixture
def image(tmp_path):
    put(tmp_path, "/var/lib/dpkg/status", STATUS)
    return tmp_path


def test_empty_supported_roots_prove_scoped_absence_deterministically(image):
    result = inventory(image)
    assert result == inventory(image)
    assert result["complete"] and not result["errors"]
    assert result["dpkg"]["packages"] == [
        {"name": "base-files", "version": "13.8", "architecture": "amd64"}
    ]
    assert all(row["complete"] and row["present"] is False for row in result["components"].values())
    assert str(image) not in json.dumps(result)


def test_installed_status_d_records_are_sorted_and_other_states_excluded(image):
    put(
        image,
        "/var/lib/dpkg/status.d/z",
        b"Package: zlib1g\nVersion: 1.3\nStatus: hold ok installed\nArchitecture: amd64\n",
    )
    put(
        image,
        "/var/lib/dpkg/status.d/removed",
        b"Package: removed\nVersion: 1\nStatus: deinstall ok config-files\n",
    )
    rows = inventory(image)["dpkg"]["packages"]
    assert [row["name"] for row in rows] == ["base-files", "zlib1g"]


def test_affected_component_positive_controls_and_suid_metadata(image, monkeypatch):
    files = {
        "infocmp": "/usr/bin/infocmp",
        "systemd_homed": "/usr/lib/systemd/systemd-homework",
        "mount": "/usr/bin/mount",
        "nsenter": "/usr/bin/nsenter",
        "libacl": "/usr/lib/x86_64-linux-gnu/libacl.so.1",
        "libmount": "/usr/lib/x86_64-linux-gnu/libmount.so.1",
        "perl_archive_tar": "/usr/share/perl/5.40.1/Archive/Tar.pm",
        "pip_vendor_msgpack_c_extension": "/app/.venv/lib/python3.12/site-packages/pip/_vendor/msgpack/_cmsgpack.cpython-312-x86_64-linux-gnu.so",
        "pip_vendor_setuptools_package_index": "/usr/local/lib/python3.12/site-packages/pip/_vendor/setuptools/package_index.py",
        "standalone_setuptools_package_index": "/app/.venv/lib/python3.12/site-packages/setuptools/package_index.py",
    }
    for path in files.values():
        put(image, path)
    # Some test filesystems strip setuid bits. Inject only stat metadata, never
    # make or execute a privileged fixture program.
    target = (image / "usr/bin/mount").stat()
    original = os.fstat

    def observed_stat(fd):
        value = original(fd)
        if (value.st_dev, value.st_ino) != (target.st_dev, target.st_ino):
            return value
        fields = {
            name: getattr(value, name)
            for name in (
                "st_mode",
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
                "st_uid",
                "st_gid",
            )
        }
        fields["st_mode"] = (value.st_mode & ~0o7777) | 0o4755
        return SimpleNamespace(**fields)

    monkeypatch.setattr(os, "fstat", observed_stat)
    result = inventory(image)
    assert result["complete"], result["errors"]
    for component, path in files.items():
        group = result["components"][component]
        assert group["complete"] and group["present"]
        record = next(row for row in group["files"] if row["path"] == path)
        assert record["sha256"] == hashlib.sha256(b"fixture component").hexdigest()
        assert record["size_bytes"] == len(b"fixture component")
    record = next(row for row in result["components"]["mount"]["files"] if row["present"])
    assert record["suid"] and not record["sgid"] and record["mode"] == "4755"


@pytest.mark.parametrize("base", ["/usr/local/lib", "/app/.venv/lib"])
def test_both_python_installation_locations_are_checked_without_imports(image, base):
    vendor = base + "/python3.12/site-packages/pip/_vendor"
    put(image, vendor + "/msgpack/_cmsgpack.abi3.so")
    put(image, vendor + "/setuptools/package_index.py")
    put(image, vendor + "/vendor.txt", b"msgpack==1.1.2\nsetuptools==70.3.0\n    urllib3==2.7.0\n")
    result = inventory(image)
    assert result["complete"] and result["components"]["pip_vendor_msgpack_c_extension"]["present"]
    assert result["components"]["pip_vendor_setuptools_package_index"]["present"]
    assert not result["components"]["standalone_setuptools_package_index"]["present"]
    assert result["pip_vendor_manifests"][0]["versions"] == {
        "msgpack": "1.1.2",
        "setuptools": "70.3.0",
        "urllib3": "2.7.0",
    }


def test_usrmerge_and_absolute_symlinks_stay_inside_the_selected_image(image):
    put(image, "/usr/bin/infocmp", b"image executable")
    (image / "bin").symlink_to("/usr/bin", target_is_directory=True)
    target = put(image, "/usr/lib/x86_64-linux-gnu/libmount.so.1.1")
    (image / "usr/lib/libmount.so.1").symlink_to("x86_64-linux-gnu/" + target.name)
    result = inventory(image)
    assert result["complete"], result["errors"]
    alias = next(
        row for row in result["components"]["infocmp"]["files"] if row["path"] == "/bin/infocmp"
    )
    assert alias["resolved_path"] == "/usr/bin/infocmp"
    assert alias["sha256"] == hashlib.sha256(b"image executable").hexdigest()
    assert alias["symlinks"] == [{"path": "/bin", "target": "/usr/bin"}]


@pytest.mark.parametrize(
    "target,code",
    [
        ("/etc/private", "outside_inventory_scope"),
        ("missing", "unresolved_symlink"),
        ("infocmp", "symlink_limit"),
    ],
)
def test_unsafe_broken_and_looping_links_never_mean_absence(image, target, code):
    put(image, "/etc/private", b"NEVER_READ_THIS_SECRET")
    path = image / "usr/bin/infocmp"
    path.parent.mkdir(parents=True)
    path.symlink_to(target)
    result = inventory(image)
    assert not result["complete"]
    row = next(
        row for row in result["components"]["infocmp"]["files"] if row["path"] == "/usr/bin/infocmp"
    )
    assert row["present"] is None and row["error"] == code
    assert "NEVER_READ_THIS_SECRET" not in json.dumps(result)


def test_valid_usrmerge_target_with_missing_component_is_actual_absence(image):
    (image / "usr/bin").mkdir(parents=True)
    (image / "bin").symlink_to("/usr/bin", target_is_directory=True)
    result = inventory(image)
    assert result["complete"]
    assert result["components"]["infocmp"]["present"] is False


def test_nonregular_component_is_rejected_without_reading_fifo(image):
    path = image / "usr/bin/infocmp"
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    result = inventory(image)
    assert not result["complete"]
    assert result["components"]["infocmp"]["present"] is None


def test_permission_failure_is_unknown_and_all_opens_are_read_only(image, monkeypatch):
    put(image, "/usr/bin/infocmp")
    original = os.open

    def checked_open(path, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
        if path == "infocmp":
            raise PermissionError
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", checked_open)
    result = inventory(image)
    assert not result["complete"]
    assert result["components"]["infocmp"]["present"] is None


@pytest.mark.parametrize(
    "limits",
    [
        Limits(file_bytes=2),
        Limits(entries=1),
        Limits(status_bytes=2),
        Limits(total_bytes=2),
        Limits(packages=0),
        Limits(output_bytes=128),
    ],
)
def test_resource_limits_fail_closed(image, limits):
    put(image, "/usr/bin/infocmp", b"long component")
    put(image, "/usr/share/perl/one.pm")
    put(image, "/usr/share/perl/two.pm")
    result = inventory(image, limits=limits)
    assert not result["complete"] and result["errors"]


def test_directory_depth_limit_cannot_hide_a_perl_module(image):
    put(image, "/usr/share/perl/5.40/extra/Archive/Tar.pm")
    result = inventory(image, limits=Limits(depth=1))
    assert not result["complete"]
    assert result["components"]["perl_archive_tar"]["present"] is None


@pytest.mark.parametrize(
    "base",
    [
        "/etc/perl",
        "/usr/share/perl5",
        "/usr/lib/x86_64-linux-gnu/perl-base",
        "/usr/local/lib/x86_64-linux-gnu/perl/5.40.1",
        "/usr/local/share/perl/5.40.1",
    ],
)
def test_system_perl_search_locations_have_positive_controls(image, base):
    path = base + "/Archive/Tar.pm"
    put(image, path)
    result = inventory(image)
    assert result["complete"], result["errors"]
    assert result["components"]["perl_archive_tar"]["present"]
    assert result["components"]["perl_archive_tar"]["files"][0]["path"] == path


def test_nested_native_module_is_detected(image):
    put(
        image,
        "/app/.venv/lib/python3.12/site-packages/pip/_vendor/msgpack/native/_cmsgpack.abi3.so",
    )
    result = inventory(image)
    assert result["complete"]
    assert result["components"]["pip_vendor_msgpack_c_extension"]["present"]


def test_standalone_distribution_versions_do_not_replace_vendor_evidence(image):
    site = "/app/.venv/lib/python3.12/site-packages"
    put(image, site + "/pip-26.2.2.dist-info/METADATA", b"Name: pip\nVersion: 26.2.2\n")
    put(
        image,
        site + "/setuptools-84.2.1.dist-info/METADATA",
        b"Name: setuptools\nVersion: 84.2.1\n",
    )
    put(image, site + "/setuptools/package_index.py")
    put(image, site + "/pip/_vendor/vendor.txt", b"setuptools==70.3.0\n")
    result = inventory(image)
    assert result["complete"]
    assert [(row["name"], row["version"]) for row in result["python_distributions"]] == [
        ("pip", "26.2.2"),
        ("setuptools", "84.2.1"),
    ]
    assert result["pip_vendor_manifests"][0]["versions"] == {"setuptools": "70.3.0"}
    assert result["components"]["standalone_setuptools_package_index"]["present"]
    assert not result["components"]["pip_vendor_setuptools_package_index"]["present"]


def test_no_environment_or_pip_import_is_used(image, monkeypatch):
    original = builtins.__import__

    def checked_import(name, *args, **kwargs):
        assert not name.startswith("pip")
        return original(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("environment read")

    monkeypatch.setattr(builtins, "__import__", checked_import)
    monkeypatch.setattr(os, "getenv", forbidden)
    assert inventory(image)["complete"]


def test_cli_emits_json_with_complete_or_failed_exit_codes(image):
    script = ROOT / "scripts/image_security_inventory.py"
    command = [sys.executable, "-B", str(script), "--root", str(image)]
    result = subprocess.run(command, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)["complete"]
    (image / "var/lib/dpkg/status").unlink()
    result = subprocess.run(command, text=True, capture_output=True, timeout=10)
    assert result.returncode == 2 and not json.loads(result.stdout)["complete"]


@pytest.mark.parametrize(
    "path,component",
    [
        ("/usr/local/lib/python3.12/site-packages/pip/__init__.py", "global_pip"),
        ("/usr/local/lib/python3.12/site-packages/pip.py", "global_pip"),
        ("/usr/local/lib/python3.12/site-packages/pip.pyc", "global_pip"),
        ("/usr/local/bin/pip3.12", "global_pip"),
        ("/usr/local/lib/python3.12/ensurepip/__init__.py", "global_ensurepip"),
        ("/usr/local/lib/python3.12/ensurepip.py", "global_ensurepip"),
        (
            "/usr/local/lib/python3.12/ensurepip/_bundled/pip-25.0.1-py3-none-any.whl",
            "global_bundled_installers",
        ),
        ("/usr/share/python-wheels/pip-25.0.1-py3-none-any.whl", "global_bundled_installers"),
        ("/usr/local/bin/uv", "global_uv"),
        ("/usr/local/bin/uvx", "global_uv"),
    ],
)
def test_global_installer_code_is_detected_without_distribution_metadata(image, path, component):
    put(image, path)
    result = inventory(image)
    assert result["complete"], result["errors"]
    assert not result["python_distributions"]
    assert result["components"][component]["present"]
    policy = no_global_installers(result)
    assert not policy["passed"] and component in policy["violations"]


@pytest.mark.parametrize(
    "path,component",
    [
        ("/usr/local/lib/python3.12/site-packages/pip", "global_pip"),
        ("/usr/local/lib/python3.12/ensurepip", "global_ensurepip"),
    ],
)
def test_empty_global_module_directory_cannot_pass_the_policy(image, path, component):
    (image / path.lstrip("/")).mkdir(parents=True)
    result = inventory(image)
    assert result["complete"] and result["components"][component]["present"]
    assert component in no_global_installers(result)["violations"]


def test_global_distribution_metadata_is_independently_rejected(image):
    put(
        image,
        "/usr/local/lib/python3.12/site-packages/pip-25.0.1.dist-info/METADATA",
        b"Name: pip\nVersion: 25.0.1\n",
    )
    result = inventory(image)
    assert result["complete"]
    assert not result["components"]["global_pip"]["present"]
    assert no_global_installers(result)["violations"] == ["global_pip_distribution"]


def test_venv_pip_remains_permitted_and_incomplete_evidence_never_passes(image):
    site = "/app/.venv/lib/python3.12/site-packages"
    put(image, site + "/pip/__init__.py")
    put(image, site + "/pip-26.2.2.dist-info/METADATA", b"Name: pip\nVersion: 26.2.2\n")
    result = inventory(image)
    assert result["complete"] and no_global_installers(result)["passed"]
    (image / "var/lib/dpkg/status").unlink()
    result = inventory(image)
    assert not result["complete"]
    assert no_global_installers(result) == {
        "name": "no_global_installers",
        "passed": False,
        "violations": ["inventory_incomplete"],
    }


def test_global_installer_cli_policy_is_opt_in_and_always_emits_json(image):
    script = ROOT / "scripts/image_security_inventory.py"
    command = [sys.executable, "-B", str(script), "--root", str(image)]
    module = put(image, "/usr/local/lib/python3.12/site-packages/pip.py")
    default = subprocess.run(command, text=True, capture_output=True, timeout=10)
    assert default.returncode == 0
    assert json.loads(default.stdout)["complete"]
    assert "policy" not in json.loads(default.stdout)
    rejected = subprocess.run(
        command + ["--require-no-global-installers"], text=True, capture_output=True, timeout=10
    )
    assert rejected.returncode == 2
    value = json.loads(rejected.stdout)
    assert value["complete"] and not value["policy"]["passed"]
    module.unlink()
    accepted = subprocess.run(
        command + ["--require-no-global-installers"], text=True, capture_output=True, timeout=10
    )
    assert accepted.returncode == 0 and json.loads(accepted.stdout)["policy"]["passed"]
