"""Audit an explicit public file inventory without staging, committing or publishing.

Hash the exact candidates and reject private outputs, admitted writer/sample records,
symlinks and Markdown links unavailable in that inventory. This complements source,
asset/license and credential review; it cannot establish redistribution permission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

PRIVATE_PREFIXES = (
    ".git/",
    ".local/",
    ".agents/",
    ".codex/",
    "artifacts/",
    "captures/",
    "uploads/",
    "private-data/",
    "research/data/",
    "research/datasets/",
    "research/raw/",
    "research/private/",
    "research/runs/",
    "research/models/",
    "research/checkpoints/",
    "research/prepared/",
    "research/results/",
)
PRIVATE_FILES = {
    "AGENTS.md",
    "PROJECT_STATUS.md",
    "NEXT_CHAT_HANDOFF.md",
    "research/manifests/iiit-hw-dev-r1.yaml",
    "research/manifests/writers-v1.yaml",
    "docs/history/project-status-20261024.md",
}
PRIVATE_SUFFIXES = {".ckpt", ".onnx", ".pt", ".pth", ".safetensors", ".pickle", ".pkl"}
LINK = re.compile(r"\]\(([^)]+)\)")


def inventory_paths(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("inventory_requires_nonempty_path_list")
    paths = []
    for name in value:
        if (
            not isinstance(name, str)
            or not name
            or "\\" in name
            or any(ord(ch) < 32 for ch in name)
            or PurePosixPath(name).is_absolute()
            or ".." in PurePosixPath(name).parts
            or str(PurePosixPath(name)) != name
        ):
            raise ValueError("inventory_requires_normalized_relative_paths")
        paths.append(name)
    if len(paths) != len(set(paths)):
        raise ValueError("duplicate_inventory_path")
    return sorted(paths)


def private_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        name in PRIVATE_FILES
        or path.name in {"AGENTS.md", "PROJECT_STATUS.md", "NEXT_CHAT_HANDOFF.md"}
        or name.startswith(PRIVATE_PREFIXES)
        or (
            path.parent == PurePosixPath("research")
            and path.suffix == ".md"
            and path.name != "README.md"
        )
        or path.suffix.lower() in PRIVATE_SUFFIXES
        or (
            path.name.startswith(".env")
            and not path.name.endswith((".example", ".sample", ".template"))
        )
        or (name.startswith("research/CHECKPOINT_") and name.endswith(".inventory.json"))
        or (
            name.startswith("research/")
            and path.name.startswith(("PAGE_TO_FONT_", "ENGLISH_VOLUNTEER_", "NEXT_CHAT_HANDOFF_"))
        )
    )


def contains_private_records(body: object) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("record_type") == "dataset_manifest":
        # Only the authored metadata control is eligible for automatic inspection.
        return bool(body.get("samples")) and body.get("dataset_id") != "split-validator-metadata"
    if body.get("record_type") == "writer_registry":
        if not isinstance(body.get("identities"), list):
            return True
        return any(
            not isinstance(row, dict) or row.get("dataset_id") != "split-validator-metadata"
            for row in body.get("identities", [])
        )
    return False


def audit(root: Path, names: list[str]) -> dict:
    names = inventory_paths(names)
    root = root.resolve()
    selected = set(names)
    files: list[dict] = []
    problems: list[dict[str, object]] = []
    for name in names:
        path = root / name
        if private_path(name):
            problems.append({"path": name, "code": "private_file"})
            continue
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root):
            problems.append({"path": name, "code": "symlink_not_publishable"})
            continue
        if not path.resolve().is_relative_to(root) or not path.is_file():
            problems.append({"path": name, "code": "missing_or_unsafe_file"})
            continue
        before = path.stat()
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        raw = path.read_bytes() if before.st_size <= 2 * 1024 * 1024 else None
        after = path.stat()

        def identity(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

        if identity(before) != identity(after):
            problems.append({"path": name, "code": "file_changed_during_audit"})
            continue
        text = None
        if raw is not None:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                pass
        files.append(
            {
                "path": name,
                "size_bytes": after.st_size,
                "sha256": checksum,
                "manual_binary_or_large_file_review": text is None,
            }
        )
        if text is not None and path.suffix.lower() in {".json", ".yaml", ".yml"}:
            try:
                body = json.loads(text)
            except ValueError:
                body = None
            if contains_private_records(body):
                problems.append({"path": name, "code": "private_sample_or_writer_records"})
        if text is not None and path.suffix.lower() == ".md":
            for number, line in enumerate(text.splitlines(), start=1):
                if "/Users/" in line or "/Volumes/" in line:
                    problems.append(
                        {
                            "path": name,
                            "line": number,
                            "code": "machine_specific_documentation_path",
                        }
                    )
            for match in LINK.finditer(text):
                target = match[1].strip("<>").split("#")[0].split("?")[0]
                if not target or "://" in target or target.startswith(("mailto:", "data:")):
                    continue
                resolved = (path.parent / unquote(target)).resolve()
                relative = (
                    resolved.relative_to(root).as_posix() if resolved.is_relative_to(root) else None
                )
                present = (
                    relative == "."
                    or relative in selected
                    or (
                        relative is not None
                        and any(n.startswith(relative.rstrip("/") + "/") for n in names)
                    )
                )
                if not present:
                    problems.append(
                        {
                            "path": name,
                            "line": text.count("\n", 0, match.start()) + 1,
                            "code": "link_not_in_public_inventory",
                        }
                    )
    return {
        "schema_version": 1,
        "scope": "explicit_public_file_inventory",
        "automatic_checks_passed": not problems,
        "manual_review_required": [
            "exact_diff",
            "source_and_asset_licenses",
            "binary_assets",
            "credential_scan",
        ],
        "files": files,
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    report = audit(args.root, json.loads(args.inventory.read_text()))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Audited {len(report['files'])} files; {len(report['problems'])} publication problems.")
    return 0 if report["automatic_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
