"""R6 audited source snapshots. Explicit, metadata-only, after Part B readiness."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from research.english.r5_data.common import canonical_hash, checked, digest_file, write_json
from research.english.r5_eval.snapshot import inventory as compatible_inventory
from research.pretrained.artifacts import ROOT


def inventory(root: Path = ROOT) -> dict:
    result = compatible_inventory(root)
    for name in (
        "scripts/train_english_r6_research.sh",
        "research/ENGLISH_R6_CONTRACT.md",
        "research/ENGLISH_R6_MODEL.md",
    ):
        result[name] = digest_file(checked(root, name))
    return dict(sorted(result.items()))


def verify(run: Path) -> tuple[Path, dict]:
    manifest = json.loads(checked(run, "execution-source.json").read_text())
    archive_path = checked(run, "execution-source.zip")
    if (
        manifest.get("version") != "r6-source-v1"
        or digest_file(archive_path) != manifest["archive_sha256"]
    ):
        raise ValueError("r6_source_archive_changed")
    target = Path(manifest["snapshot_root"])
    expected = Path("/private/tmp") / (
        "penforge-r6-source-" + canonical_hash(str(run.resolve()))[:20]
    )
    if target != expected:
        raise ValueError("r6_source_cache_location_changed")
    if target.is_symlink():
        raise ValueError("r6_source_cache_unsafe")
    if not target.exists():
        temporary = Path(tempfile.mkdtemp(prefix=".r6-restore-", dir=target.parent))
        try:
            with zipfile.ZipFile(archive_path) as archive:
                if set(archive.namelist()) != set(manifest["files"]) or len(
                    archive.namelist()
                ) != len(manifest["files"]):
                    raise ValueError("r6_archive_file_inventory_changed")
                for name, pin in manifest["files"].items():
                    path = temporary / name
                    if not path.resolve().is_relative_to(temporary):
                        raise ValueError("r6_unsafe_snapshot_member")
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.write_bytes(archive.read(name))
                    path.chmod(0o600)
                    if digest_file(path) != pin:
                        raise ValueError("r6_snapshot_member_changed")
            temporary.rename(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    observed = {}
    for path in target.rglob("*"):
        if path.is_symlink():
            raise ValueError("r6_source_cache_symlink")
        if path.is_file() and "__pycache__" not in path.parts:
            observed[str(path.relative_to(target))] = digest_file(path)
    if observed != manifest["files"]:
        raise ValueError("r6_frozen_source_cache_drift")
    return target, manifest


def freeze(run: Path, *, source_root: Path = ROOT, ready: bool = False) -> dict:
    if not ready:
        raise ValueError("r6_part_b_verified_readiness_required_before_source_freeze")
    if (run / "execution-source.json").exists():
        raise ValueError("r6_source_snapshot_already_frozen")
    pins = inventory(source_root)
    target = Path("/private/tmp") / (
        "penforge-r6-source-" + canonical_hash(str(run.resolve()))[:20]
    )
    if target.exists() or target.is_symlink():
        raise ValueError("r6_source_cache_collision")
    archive_path = run / "execution-source.zip"
    with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in pins:
            archive.write(checked(source_root, name), name)
    archive_path.chmod(0o600)
    if inventory(source_root) != pins:
        raise ValueError("r6_source_changed_during_freeze")
    value = {
        "version": "r6-source-v1",
        "files": pins,
        "origin": str(source_root),
        "snapshot_root": str(target),
        "archive_sha256": digest_file(archive_path),
        "model_execution": False,
    }
    write_json(run / "execution-source.json", value)
    verify(run)
    return value
