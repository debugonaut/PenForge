"""Read-only preparation checks. Never extracts features, fits or runs a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.models import DatasetManifest, WriterRegistry
from research.preparation.archive_audit import ROOT, external, file_hash, safe_name, sha
from research.preparation.freeze import validate_split_rows, writer_id
from research.validate import assert_build_eligible, digest, load_record, validate_datasets


def verify_study(rows, manifest, ledger):
    if "sha256:" + file_hash(ledger) != manifest.content_hash:
        raise ValueError("frozen_split_ledger_changed")
    expected = {s.sample_id: s for s in manifest.samples}
    public = {r["id"]: r for r in (json.loads(line) for line in ledger.read_text().splitlines())}
    if len(rows) != len(expected) or {r["id"] for r in rows} != set(expected):
        raise ValueError("private_study_does_not_match_admitted_manifest")
    for row in rows:
        safe_name(row["path"])
        sample, split = expected[row["id"]], public[row["id"]]
        derived = {
            "id": row["id"],
            "hash": row["hash"],
            "split": row["split"],
            "page_id": row["page_id"],
            "duplicate": row["duplicate"],
            "writer": writer_id(row["writer"]),
            "label_hash": sha(row["label"].encode()),
            "recognition": row["recognition"],
            "style_role": row.get("style_role"),
            "glyph_candidate": row.get("glyph_candidate", False),
        }
        if (
            derived != split
            or sample.content_hash != "sha256:" + row["hash"]
            or sample.writer_id != derived["writer"]
            or sample.split != row["split"]
            or sample.source_group_id != row["page_id"]
        ):
            raise ValueError("private_study_lineage_or_label_changed")


def check(staging: Path, config_path: Path):
    staging = external(staging)
    config = json.loads(config_path.read_text())
    preparation = json.loads((ROOT / "research/prepared/local-r1/preparation.json").read_text())
    manifest = load_record(ROOT / "research/manifests/iiit-hw-dev-r1.yaml")
    registry = load_record(ROOT / "research/manifests/writers-v1.yaml")
    if not isinstance(manifest, DatasetManifest) or not isinstance(registry, WriterRegistry):
        raise ValueError("unexpected_preparation_record_type")
    assert_build_eligible([manifest], "research_training")
    validate_datasets([manifest], registry)
    if (
        digest(config) != preparation["config_hash"]
        or digest(manifest) != preparation["manifest_hash"]
        or digest(registry) != preparation["registry_hash"]
    ):
        raise ValueError("frozen_preparation_config_or_records_changed")
    rows = [json.loads(line) for line in (staging / "study-r1.jsonl").read_text().splitlines()]
    validate_split_rows(rows)
    verify_study(rows, manifest, ROOT / "research/prepared/local-r1/split-ids.jsonl")
    missing = sum(not (staging / config["corpus"] / row["path"]).is_file() for row in rows)
    if missing:
        raise ValueError("staged_images_missing_rebuild_preparation")
    return {
        "status": "prepared",
        "admitted_samples": len(rows),
        "config_hash": digest(config),
        "manifest_hash": digest(manifest),
        "split_ids_hash": preparation["split_ids_hash"],
        "recognition_splits": {
            s: {"samples": v["samples"], "writers": v["writers"]}
            for s, v in preparation["recognition_splits"].items()
        },
        "source_bytes_rehashed": False,
        "feature_extraction": False,
        "training": "not_run",
        "model_inference": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "research/configs/local-r1.json")
    args = parser.parse_args()
    print(json.dumps(check(args.staging, args.config), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
