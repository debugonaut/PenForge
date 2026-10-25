"""Bounded, read-only component evidence for a built Linux image.

This is inventory, not a vulnerability exemption. Missing, unreadable, unsafe or
truncated evidence fails closed. No environment, credentials or package imports
are inspected. Absolute symlinks are resolved within the selected image root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

SCOPES = (
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/usr/bin",
    "/usr/sbin",
    "/usr/local/bin",
    "/usr/lib",
    "/usr/lib64",
    "/usr/local/lib",
    "/usr/share/perl",
    "/usr/share/perl5",
    "/usr/local/share/perl",
    "/usr/share/python-wheels",
    "/etc/perl",
    "/app/.venv/lib",
    "/var/lib/dpkg",
)
PYTHON = re.compile(r"python[0-9]+\.[0-9]+")
TRIPLET = re.compile(r"[a-zA-Z0-9_]+-linux-[a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class Limits:
    file_bytes: int = 64 * 1024 * 1024
    status_bytes: int = 16 * 1024 * 1024
    total_bytes: int = 256 * 1024 * 1024
    entries: int = 30000
    depth: int = 10
    symlinks: int = 16
    packages: int = 1024
    output_bytes: int = 256 * 1024


class InventoryError(Exception):
    pass


class ImageRoot:
    def __init__(self, root: Path, limits: Limits):
        self.root = root.resolve(strict=True)
        self.limits = limits
        self.errors: list[dict[str, str]] = []
        self.searches: dict[str, dict] = {}
        self.entries = 0
        self.bytes_read = 0

    def error(self, path: str, code: str) -> None:
        row = {"path": path, "code": code}
        if row not in self.errors:
            self.errors.append(row)

    @staticmethod
    def in_scope(parts: list[str], *, ancestor: bool = False) -> bool:
        path = "/" + "/".join(parts)
        return any(
            path == scope
            or path.startswith(scope + "/")
            or (ancestor and (scope.startswith(path + "/") or path == "/"))
            for scope in SCOPES
        )

    @contextmanager
    def open(self, logical: str, *, directory: bool = False):
        if not logical.startswith("/") or ".." in PurePosixPath(logical).parts:
            raise InventoryError("invalid_path")
        pending = deque((part, False) for part in PurePosixPath(logical).parts[1:])
        parts: list[str] = []
        links: list[dict[str, str]] = []
        fds = [os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
        file_fd = None
        try:
            while pending:
                name, link_target = pending.popleft()
                if name in ("", "."):
                    continue
                if name == "..":
                    if len(fds) == 1:
                        raise InventoryError("symlink_root_escape")
                    os.close(fds.pop())
                    parts.pop()
                    continue
                candidate = parts + [name]
                if not self.in_scope(candidate, ancestor=bool(pending) or directory):
                    raise InventoryError("outside_inventory_scope")
                try:
                    info = os.stat(name, dir_fd=fds[-1], follow_symlinks=False)
                except FileNotFoundError:
                    if link_target:
                        raise InventoryError("unresolved_symlink") from None
                    raise
                if stat.S_ISLNK(info.st_mode):
                    if len(links) >= self.limits.symlinks:
                        raise InventoryError("symlink_limit")
                    try:
                        target = os.readlink(name, dir_fd=fds[-1])
                    except FileNotFoundError:
                        raise InventoryError("path_changed") from None
                    if len(target) > 4096:
                        raise InventoryError("symlink_target_limit")
                    links.append({"path": "/" + "/".join(candidate), "target": target})
                    if target.startswith("/"):
                        while len(fds) > 1:
                            os.close(fds.pop())
                        parts = []
                    pending.extendleft(
                        reversed([(part, True) for part in PurePosixPath(target).parts])
                    )
                    if pending and pending[0][0] == "/":
                        pending.popleft()
                    continue
                is_directory = bool(pending) or directory
                if is_directory and not stat.S_ISDIR(info.st_mode):
                    raise InventoryError("not_directory")
                if not is_directory and not stat.S_ISREG(info.st_mode):
                    raise InventoryError("not_regular_file")
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if is_directory:
                    flags |= os.O_DIRECTORY
                try:
                    opened = os.open(name, flags, dir_fd=fds[-1])
                except FileNotFoundError:
                    raise InventoryError("path_changed") from None
                observed = os.fstat(opened)
                if (observed.st_dev, observed.st_ino) != (info.st_dev, info.st_ino):
                    os.close(opened)
                    raise InventoryError("path_changed")
                parts.append(name)
                if pending:
                    fds.append(opened)
                else:
                    file_fd = opened
            if file_fd is None:
                raise InventoryError("empty_path")
            yield file_fd, "/" + "/".join(parts), links
        finally:
            if file_fd is not None:
                os.close(file_fd)
            for fd in reversed(fds):
                os.close(fd)

    def file(self, path: str, *, content_limit: int | None = None) -> tuple[dict, bytes]:
        row: dict = {"path": path, "present": None}
        try:
            with self.open(path) as (fd, resolved, links):
                before = os.fstat(fd)
                if self.bytes_read > self.limits.total_bytes:
                    raise InventoryError("inventory_read_byte_limit")
                maximum = self.limits.file_bytes if content_limit is None else content_limit
                if before.st_size > maximum:
                    raise InventoryError("file_byte_limit")
                digest = hashlib.sha256()
                chunks = []
                count = 0
                while chunk := os.read(
                    fd,
                    min(65536, maximum - count + 1, self.limits.total_bytes - self.bytes_read + 1),
                ):
                    count += len(chunk)
                    self.bytes_read += len(chunk)
                    if self.bytes_read > self.limits.total_bytes:
                        raise InventoryError("inventory_read_byte_limit")
                    if count > maximum:
                        raise InventoryError("file_byte_limit")
                    digest.update(chunk)
                    if content_limit is not None:
                        chunks.append(chunk)
                after = os.fstat(fd)
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise InventoryError("file_changed")
                row.update(
                    present=True,
                    resolved_path=resolved,
                    symlinks=links,
                    sha256=digest.hexdigest(),
                    size_bytes=count,
                    mode=f"{stat.S_IMODE(before.st_mode):04o}",
                    uid=before.st_uid,
                    gid=before.st_gid,
                    suid=bool(before.st_mode & stat.S_ISUID),
                    sgid=bool(before.st_mode & stat.S_ISGID),
                )
                return row, b"".join(chunks)
        except FileNotFoundError:
            row["present"] = False
        except (OSError, InventoryError) as error:
            code = str(error) if isinstance(error, InventoryError) else "inaccessible_path"
            self.error(path, code)
            row["error"] = code
        return row, b""

    def directory(self, path: str) -> list[str]:
        row: dict = {"path": path, "present": None, "complete": False}
        names = []
        try:
            with self.open(path, directory=True) as (fd, _, _):
                row["present"] = True
                with os.scandir(fd) as entries:
                    for entry in entries:
                        self.entries += 1
                        if self.entries > self.limits.entries:
                            raise InventoryError("directory_entry_limit")
                        names.append(entry.name)
                row["complete"] = True
        except FileNotFoundError:
            row.update(present=False, complete=True)
        except (OSError, InventoryError) as error:
            code = str(error) if isinstance(error, InventoryError) else "inaccessible_directory"
            self.error(path, code)
            row["error"] = code
        self.searches[path] = row
        return sorted(names)

    def walk(self, roots):
        pending = [(path, 0) for path in sorted(roots)]
        while pending:
            base, depth = pending.pop()
            for name in self.directory(base):
                path = base + "/" + name
                try:
                    with self.open(path, directory=True):
                        pass
                except FileNotFoundError:
                    self.error(path, "path_disappeared")
                    continue
                except InventoryError as error:
                    if str(error) == "not_directory":
                        yield path
                    else:
                        self.error(path, str(error))
                    continue
                except OSError:
                    self.error(path, "inaccessible_path")
                    continue
                if depth >= self.limits.depth:
                    self.error(path, "directory_depth_limit")
                else:
                    pending.append((path, depth + 1))


def package_inventory(fs: ImageRoot) -> dict:
    before = len(fs.errors)
    candidates = ["/var/lib/dpkg/status"]
    candidates += [
        "/var/lib/dpkg/status.d/" + name for name in fs.directory("/var/lib/dpkg/status.d")
    ]
    packages: dict[tuple[str, str], dict[str, str]] = {}
    sources = []
    found = False
    for path in candidates:
        row, data = fs.file(path, content_limit=fs.limits.status_bytes)
        sources.append(row)
        if not row["present"]:
            continue
        found = True
        try:
            text = data.decode("utf-8")
            for block in text.split("\n\n"):
                fields = dict(
                    line.split(": ", 1)
                    for line in block.splitlines()
                    if line and not line[0].isspace() and ": " in line
                )
                if fields.get("Status", "").split()[-2:] != ["ok", "installed"]:
                    continue
                name, version = fields["Package"], fields["Version"]
                architecture = fields.get("Architecture", "unknown")
                if (
                    not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", name)
                    or not version
                    or len(version) > 200
                ):
                    raise ValueError
                key = (name, architecture)
                value = {"name": name, "version": version, "architecture": architecture}
                if key in packages and packages[key] != value:
                    raise ValueError
                packages[key] = value
                if len(packages) > fs.limits.packages:
                    raise InventoryError("package_limit")
        except (UnicodeError, KeyError, ValueError, InventoryError):
            fs.error(path, "invalid_or_bounded_package_database")
    if not found or not packages:
        fs.error("/var/lib/dpkg", "installed_package_database_missing")
    return {
        "complete": len(fs.errors) == before,
        "sources": sources,
        "packages": [packages[key] for key in sorted(packages)],
    }


def inventory(root: Path = Path("/"), *, limits: Limits = Limits()) -> dict:
    fs = ImageRoot(root, limits)
    components = {}

    def group(name: str, paths, errors_before: int | None = None, *, directories=()):
        start = len(fs.errors) if errors_before is None else errors_before
        rows = [fs.file(path)[0] for path in sorted(set(paths))]
        directory_rows = []
        for path in sorted(set(directories)):
            fs.directory(path)
            directory_rows.append(dict(fs.searches[path]))
        present = any(row["present"] is True for row in [*rows, *directory_rows])
        complete = len(fs.errors) == start
        components[name] = {
            "complete": complete,
            "present": present if complete or present else None,
            "files": rows,
        }
        if directory_rows:
            components[name]["directories"] = directory_rows

    dpkg = package_inventory(fs)
    group("infocmp", ["/usr/bin/infocmp", "/bin/infocmp"])
    group(
        "systemd_homed",
        [
            base + "/" + binary
            for base in ("/usr/lib/systemd", "/lib/systemd")
            for binary in ("systemd-homed", "systemd-homework")
        ],
    )
    group("mount", ["/usr/bin/mount", "/bin/mount"])
    group("nsenter", ["/usr/bin/nsenter", "/bin/nsenter"])

    library_roots = ["/usr/lib", "/usr/lib64", "/lib", "/lib64"]
    libraries: dict[str, list[str]] = {"libacl": [], "libmount": []}
    start = len(fs.errors)
    for base in tuple(library_roots):
        for name in fs.directory(base):
            if TRIPLET.fullmatch(name):
                library_roots.append(base + "/" + name)
    for base in sorted(set(library_roots)):
        for name in fs.directory(base):
            for library in libraries:
                if name == library + ".so" or name.startswith(library + ".so."):
                    libraries[library].append(base + "/" + name)
    for name, paths in libraries.items():
        group(name, paths, start)

    start = len(fs.errors)
    perl_roots = {"/etc/perl", "/usr/share/perl", "/usr/share/perl5", "/usr/local/share/perl"}
    for base in ("/usr/lib", "/usr/local/lib"):
        for name in fs.directory(base):
            if name.startswith("perl") or name in {"site_perl", "vendor_perl"}:
                perl_roots.add(base + "/" + name)
            elif TRIPLET.fullmatch(name):
                for child in fs.directory(base + "/" + name):
                    if child.startswith("perl") or child in {"site_perl", "vendor_perl"}:
                        perl_roots.add(base + "/" + name + "/" + child)
    archives = [path for path in fs.walk(perl_roots) if path.endswith("/Archive/Tar.pm")]
    group("perl_archive_tar", archives, start)

    start = len(fs.errors)
    msgpack = []
    vendor_package_index = []
    standalone_package_index = []
    vendor_manifests = []
    distributions = []
    global_pip_paths = ["/usr/local/bin/pip", "/usr/local/bin/pip3"]
    global_pip_directories = []
    global_ensurepip_paths = []
    global_ensurepip_directories = []
    global_bundled = []
    global_start = len(fs.errors)
    for name in fs.directory("/usr/local/bin"):
        if re.fullmatch(r"pip[0-9]+(?:\.[0-9]+)?", name):
            global_pip_paths.append("/usr/local/bin/" + name)
    for path in fs.walk(["/usr/share/python-wheels"]):
        if re.fullmatch(r"(?:pip|setuptools|wheel)-[^/]+\.(?:whl|zip)", PurePosixPath(path).name):
            global_bundled.append(path)
    for base in ("/usr/local/lib", "/app/.venv/lib"):
        for version in fs.directory(base):
            if not PYTHON.fullmatch(version):
                continue
            site = base + "/" + version + "/site-packages"
            vendor = site + "/pip/_vendor"
            if base == "/usr/local/lib":
                global_pip_directories.append(site + "/pip")
                global_pip_paths.extend(
                    [
                        site + "/pip.py",
                        site + "/pip.pyc",
                        site + "/pip/__init__.py",
                        site + "/pip/__main__.py",
                    ]
                )
                ensurepip = base + "/" + version + "/ensurepip"
                global_ensurepip_directories.append(ensurepip)
                global_ensurepip_paths.extend(
                    [
                        ensurepip + ".py",
                        ensurepip + ".pyc",
                        ensurepip + "/__init__.py",
                        ensurepip + "/__main__.py",
                    ]
                )
                for path in fs.walk([ensurepip + "/_bundled"]):
                    if path.endswith((".whl", ".zip")):
                        global_bundled.append(path)
            for path in fs.walk([vendor + "/msgpack"]):
                if (
                    path.endswith((".so", ".pyd", ".dll", ".dylib"))
                    or ".so." in PurePosixPath(path).name
                ):
                    msgpack.append(path)
            vendor_package_index.append(vendor + "/setuptools/package_index.py")
            standalone_package_index.append(site + "/setuptools/package_index.py")
            for name in fs.directory(site):
                match = re.fullmatch(r"(pip|setuptools)-[^/]{1,128}\.dist-info", name)
                if not match:
                    continue
                path = site + "/" + name + "/METADATA"
                metadata, source = fs.file(path, content_limit=262144)
                try:
                    fields = dict(
                        re.findall(
                            r"^(Name|Version): ([^\r\n]+)$", source.decode("utf-8"), re.MULTILINE
                        )
                    )
                    if (
                        fields["Name"].casefold() != match[1]
                        or not 1 <= len(fields["Version"]) <= 200
                    ):
                        raise ValueError
                    distributions.append(
                        {
                            "name": match[1],
                            "version": fields["Version"],
                            "site_packages": site,
                            "metadata": metadata,
                        }
                    )
                except (UnicodeError, KeyError, ValueError):
                    fs.error(path, "invalid_python_distribution_metadata")
            row, data = fs.file(vendor + "/vendor.txt", content_limit=65536)
            if row["present"]:
                try:
                    versions = dict(
                        re.findall(
                            r"^\s*(msgpack|setuptools|urllib3|certifi)==([^\s]+)\s*$",
                            data.decode("utf-8"),
                            re.MULTILINE,
                        )
                    )
                    vendor_manifests.append({"file": row, "versions": versions})
                except UnicodeError:
                    fs.error(vendor + "/vendor.txt", "invalid_vendor_manifest")
    group("pip_vendor_msgpack_c_extension", msgpack, start)
    group("pip_vendor_setuptools_package_index", vendor_package_index, start)
    group("standalone_setuptools_package_index", standalone_package_index, start)
    group("global_pip", global_pip_paths, global_start, directories=global_pip_directories)
    group(
        "global_ensurepip",
        global_ensurepip_paths,
        global_start,
        directories=global_ensurepip_directories,
    )
    group("global_bundled_installers", global_bundled, global_start)
    group("global_uv", ["/usr/local/bin/uv", "/usr/local/bin/uvx"], global_start)
    result = {
        "schema_version": 1,
        "complete": not fs.errors,
        "limits": asdict(limits),
        "scopes": list(SCOPES),
        "dpkg": dpkg,
        "components": components,
        "pip_vendor_manifests": vendor_manifests,
        "python_distributions": distributions,
        "searches": [fs.searches[path] for path in sorted(fs.searches)],
        "errors": sorted(fs.errors, key=lambda row: (row["path"], row["code"])),
    }
    return bounded_output(result, limits.output_bytes)


def bounded_output(result: dict, maximum: int) -> dict:
    if len(json.dumps(result, sort_keys=True).encode()) > maximum:
        return {"schema_version": 1, "complete": False, "errors": [{"code": "output_byte_limit"}]}
    return result


def no_global_installers(result: dict) -> dict:
    """Only global Python build installers; unrelated system tools are out of scope."""
    if not result.get("complete"):
        return {
            "name": "no_global_installers",
            "passed": False,
            "violations": ["inventory_incomplete"],
        }
    violations = []
    for name in ("global_pip", "global_ensurepip", "global_bundled_installers", "global_uv"):
        component = result.get("components", {}).get(name, {})
        if not component.get("complete") or component.get("present") is not False:
            violations.append(name)
    if any(
        row.get("name") == "pip" and row.get("site_packages", "").startswith("/usr/local/lib/")
        for row in result.get("python_distributions", [])
    ):
        violations.append("global_pip_distribution")
    return {
        "name": "no_global_installers",
        "passed": not violations,
        "violations": sorted(violations),
    }


def minimal_runtime_components(result: dict) -> dict:
    """Require scoped absence of unnecessary affected tools, in addition to installers."""
    violations = no_global_installers(result)["violations"]
    for name in (
        "infocmp",
        "mount",
        "nsenter",
        "libacl",
        "libmount",
        "systemd_homed",
        "perl_archive_tar",
    ):
        component = result.get("components", {}).get(name, {})
        if component.get("complete") is not True or component.get("present") is not False:
            violations.append(name)
    return {
        "name": "minimal_runtime_components",
        "passed": not violations,
        "violations": sorted(violations),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument(
        "--require-no-global-installers",
        action="store_true",
        help="Require no global pip, ensurepip/bundled installers or uv; venv pip is permitted.",
    )
    parser.add_argument("--require-minimal-runtime", action="store_true")
    parser.add_argument("--image-id", help="Exact image config ID supplied by the Docker host.")
    args = parser.parse_args()
    if args.image_id and not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id):
        parser.error("image-id must be an exact sha256 config ID")
    try:
        result = inventory(args.root)
    except (OSError, ValueError, RuntimeError):
        result = {
            "schema_version": 1,
            "complete": False,
            "errors": [{"code": "invalid_image_root"}],
        }
    if args.image_id:
        result["image_config_id"] = args.image_id
    if args.require_no_global_installers or args.require_minimal_runtime:
        policy = (
            minimal_runtime_components if args.require_minimal_runtime else no_global_installers
        )
        result["policy"] = policy(result)
        result = bounded_output(result, Limits().output_bytes)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    passed = result["complete"] and (
        not (args.require_no_global_installers or args.require_minimal_runtime)
        or result.get("policy", {}).get("passed", False)
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
