"""Freeze audited source privately, then launch explicit R5 stages from that copy.

Concurrent chats can keep editing their own source without mutating an active
experiment. This copies source/metadata only: never images, weights, runs or secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from research.english.r5_data.common import canonical_hash, digest_file, external, write_json
from research.pretrained.artifacts import ROOT


def inventory(root: Path) -> dict[str, str]:
    paths = [
        root / "pyproject.toml",
        root / "uv.lock",
        root / "scripts/train_english_r5_research.sh",
        root / "research/ENGLISH_R5_MODEL.md",
    ]
    for path in (root / "research").rglob("*"):
        relative = path.relative_to(root)
        if set(relative.parts) & {
            "runs",
            "data",
            "datasets",
            "models",
            "checkpoints",
            "prepared",
            "results",
            "__pycache__",
        }:
            continue
        metadata = len(relative.parts) > 1 and relative.parts[1] in {
            "configs",
            "manifests",
            "schemas",
        }
        if path.is_file() and (
            path.suffix == ".py" or metadata and path.suffix in {".json", ".yaml", ".toml"}
        ):
            paths.append(path)
    paths.extend((root / "packages/contracts/penforge_contracts").rglob("*.py"))
    paths.extend((root / "packages/contracts/unicode").rglob("*.json"))
    result = {}
    for path in sorted(set(paths)):
        if path.is_symlink() or not path.is_file():
            raise ValueError("r5_execution_source_requires_regular_audited_files")
        result[str(path.relative_to(root))] = digest_file(path)
    return result


def _restore_cache(target: Path, archive_path: Path, manifest: dict):
    temporary = Path(tempfile.mkdtemp(prefix=".r5-source-restore-", dir=target.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if set(archive.namelist()) != set(manifest["files"]):
                raise ValueError("r5_source_archive_inventory_changed")
            for name, expected in manifest["files"].items():
                path = temporary / name
                if not path.resolve().is_relative_to(temporary):
                    raise ValueError("r5_source_archive_unsafe_member")
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_bytes(archive.read(name))
                path.chmod(0o600)
                if digest_file(path) != expected:
                    raise ValueError("r5_source_archive_member_changed")
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def freeze_source(run: Path, *, source_root: Path = ROOT) -> tuple[Path, Path]:
    run = run.resolve()
    target = Path("/private/tmp") / ("penforge-r5-source-" + canonical_hash(str(run))[:20])
    manifest_path = run / "execution-source.json"
    archive_path = run / "execution-source.zip"
    if target.exists() or manifest_path.exists():
        if target.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("r5_source_snapshot_incomplete_or_unsafe")
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("version") != "r5-execution-source-v2" or manifest.get(
            "snapshot_root"
        ) != str(target):
            raise ValueError("r5_source_snapshot_version_changed")
        if (
            archive_path.is_symlink()
            or not archive_path.is_file()
            or digest_file(archive_path) != manifest.get("archive_sha256")
        ):
            raise ValueError("r5_source_snapshot_archive_changed")
        if not target.exists():
            _restore_cache(target, archive_path, manifest)
        observed = set()
        for path in target.rglob("*"):
            if path.is_symlink():
                raise ValueError("r5_snapshot_symlinks_forbidden")
            if path.is_file() and "__pycache__" not in path.relative_to(target).parts:
                observed.add(str(path.relative_to(target)))
        if observed != set(manifest["files"]):
            raise ValueError("r5_snapshot_file_inventory_changed")
        for name, expected in manifest["files"].items():
            path = target / name
            if (
                path.is_symlink()
                or not path.is_file()
                or not path.resolve().is_relative_to(target)
                or digest_file(path) != expected
            ):
                raise ValueError("r5_frozen_execution_source_changed")
        return target, manifest_path
    pins = inventory(source_root)
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".r5-source-", dir=target.parent))
    try:
        for name, expected in pins.items():
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source_root / name, destination)
            destination.chmod(0o600)
            if digest_file(destination) != expected:
                raise ValueError("r5_source_changed_during_snapshot")
        if inventory(source_root) != pins:
            raise ValueError("r5_source_changed_during_snapshot")
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(pins):
                archive.write(temporary / name, name)
        archive_path.chmod(0o600)
        temporary.rename(target)
        write_json(
            manifest_path,
            {
                "version": "r5-execution-source-v2",
                "origin": str(source_root),
                "snapshot_root": str(target),
                "archive_sha256": digest_file(archive_path),
                "files": pins,
                "model_execution": False,
                "excluded": [
                    "images",
                    "weights",
                    "runs",
                    "datasets",
                    "secrets",
                    "environment_files",
                ],
            },
        )
        return target, manifest_path
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preflight", "baseline", "train", "freeze", "final-test"))
    for name in ("staging", "model", "initialization", "r4-winner", "run"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--owner-run", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    raw_paths = {
        name: getattr(args, name).expanduser()
        for name in ("staging", "model", "initialization", "r4_winner", "run")
    }
    if any(path.is_symlink() for path in raw_paths.values()):
        raise ValueError("r5_snapshot_inputs_must_not_be_symlink_roots")
    paths = {name: path.resolve() for name, path in raw_paths.items()}
    external(paths["staging"])  # Enforce the actual project boundary before changing cwd.
    run = paths["run"]
    if run.is_symlink() or (
        run.is_relative_to(ROOT) and not run.is_relative_to(ROOT / "research/runs")
    ):
        raise ValueError("r5_snapshot_must_be_in_private_run_root")
    if args.stage != "preflight" and not (run / "execution-source.json").is_file():
        raise ValueError("r5_explicit_metadata_preflight_required_before_execution")
    snapshot, manifest = freeze_source(run)
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=f"{snapshot / 'packages/contracts'}:{snapshot}",
        PENFORGE_R5_LEDGER=str(ROOT / "research/runs/r5-final-test-claims"),
        PENFORGE_R5_SOURCE_MANIFEST=str(manifest),
    )
    command = [sys.executable, "-m", "research.english.r5_run", args.stage]
    for name, path in paths.items():
        command.extend(("--" + name.replace("_", "-"), str(path)))
    if args.owner_run:
        command.append("--owner-run")
    os.chdir(snapshot)
    os.execve(sys.executable, command, environment)


if __name__ == "__main__":
    main()
