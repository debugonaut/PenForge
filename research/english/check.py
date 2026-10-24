"""Verify manually downloaded English assets. No network, model loading or fitting."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.english.acquire import SOURCES, inspect
from research.english.nist import LETTERS, sha
from research.preparation.archive_audit import external


def check_sources(folder: Path) -> dict:
    folder = external(folder)
    missing = [name for name in ("by_write.zip", "by_class.zip") if not (folder / name).exists()]
    if missing:
        return {"status": "missing_manual_downloads", "missing": missing, "model_execution": False}
    return {
        "status": "source_bytes_verified_only",
        "sources": {
            name: inspect(folder / name, *SOURCES[name])
            for name in ("by_write.zip", "by_class.zip")
        },
        "dataset_admitted": False,
        "model_execution": False,
    }


def check_staging(folder: Path) -> dict:
    folder = external(folder)
    report = json.loads((folder / "preparation.json").read_text())
    mapping = folder / "study.jsonl"
    if sha(mapping.read_bytes()) != report["private_mapping_sha256"]:
        raise ValueError("nist_private_mapping_changed")
    rows = [json.loads(line) for line in mapping.read_text().splitlines()]
    seen: dict[tuple[str, str], set[str]] = {}
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("nist_empty_or_duplicate_sample_ids")
    counts = {split: {letter: 0 for letter in LETTERS} for split in ("train", "validation", "test")}
    for row in rows:
        if row["split"] not in counts or row["label"] not in LETTERS:
            raise ValueError("nist_unexpected_partition_or_label")
        counts[row["split"]][row["label"]] += 1
        if row["path"] != f"images/{row['hash']}.png":
            raise ValueError("nist_unexpected_image_reference")
        path = folder / row["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(folder) or not path.is_file():
            raise ValueError("nist_image_missing_or_symlink")
        if sha(path.read_bytes()) != row["hash"]:
            raise ValueError("nist_staged_image_changed")
        for key in ("writer", "source_group", "hash", "pixel_hash"):
            seen.setdefault((key, row[key]), set()).add(row["split"])
    if any(len(partitions) > 1 for partitions in seen.values()):
        raise ValueError("nist_writer_source_or_duplicate_leakage")
    if any(
        n != report["per_class_caps"][split]
        for split, group in counts.items()
        for n in group.values()
    ):
        raise ValueError("nist_incomplete_or_changed_letter_coverage")
    return {
        "status": "staging_integrity_verified_pending_admission",
        "samples": len(rows),
        "classes": 52,
        "writer_disjoint": True,
        "dataset_admitted": False,
        "model_execution": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--staging", type=Path)
    args = parser.parse_args()
    result = check_sources(args.sources)
    if args.staging and result["status"] != "missing_manual_downloads":
        result["staging"] = check_staging(args.staging)
    print(json.dumps(result, indent=2))
    if result["status"] == "missing_manual_downloads":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
