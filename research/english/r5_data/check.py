"""Fail-closed R5 data preflight. Does not import a model framework."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import cast

from PIL import Image

from research.english import nist
from research.english.r4_study import nist_writer_id
from research.english.r5_contract import LETTERS, Sample, TrainingInputs, validate_rows
from research.english.r5_data.common import (
    canonical_hash,
    checked,
    digest_file,
    external,
    image_identity,
    jsonl,
    load_config,
    read_jsonl,
)
from research.english.r5_eval.scoring import validation_scorer
from research.models import DatasetManifest
from research.pretrained.artifacts import ROOT
from research.validate import assert_build_eligible

MAPPINGS = {
    "train": "train.jsonl",
    "validation": "validation.jsonl",
    "test": "final-test.jsonl",
    "school_test": "school-test.jsonl",
}


def check(staging: Path, *, include_test: bool = False) -> dict:
    staging = external(staging)
    admission = json.loads(checked(staging, "admission.json").read_text())
    preparation_path = checked(staging, "preparation.json")
    if (
        admission.get("version") != "english-r5-derived-admission-v1"
        or admission.get("scope") != "local_noncommercial_isolated_letters_research"
        or digest_file(preparation_path) != admission.get("preparation_sha256")
    ):
        raise ValueError("r5_admission_or_preparation_pin_mismatch")
    report = json.loads(preparation_path.read_text())
    if (
        report.get("version") != "english-r5-preparation-v1"
        or report.get("config") != load_config()
    ):
        raise ValueError("r5_preparation_or_pinned_configuration_changed")
    if report.get("config_hash") != canonical_hash(report["config"]):
        raise ValueError("r5_config_hash_changed")
    for name, expected in report["source_code_sha256"].items():
        path = checked(ROOT, name)
        if digest_file(path) != expected:
            raise ValueError("r5_preparation_source_changed_reprepare_in_new_directory")
    required = {
        *MAPPINGS.values(),
        "train-validation.jsonl",
        "image-audit-private.jsonl",
        "history-private.jsonl",
        "exposure.json",
        "r3-admission.json",
        "r3-manifest.json",
    }
    if set(report["files"]) != required:
        raise ValueError("r5_frozen_inventory_incomplete")
    for name, expected in report["files"].items():
        if digest_file(checked(staging, name)) != expected:
            raise ValueError("r5_metadata_pin_mismatch")
    manifest = DatasetManifest.model_validate_json(
        checked(staging, "r3-manifest.json").read_bytes()
    )
    assert_build_eligible([manifest], "research_training")
    original_admission = json.loads(checked(staging, "r3-admission.json").read_text())
    if digest_file(staging / "r3-manifest.json") != original_admission["manifest_sha256"]:
        raise ValueError("r5_source_manifest_does_not_match_existing_admission")
    exposure = json.loads((staging / "exposure.json").read_text())
    if admission["source_admissions"] != exposure["admissions"]:
        raise ValueError("r5_original_admission_lineage_changed")
    for source in [*exposure["sources"], *exposure["admissions"]]:
        path = Path(source["path"])
        if path.is_symlink() or not path.is_file() or digest_file(path) != source["sha256"]:
            raise ValueError("r5_original_history_or_admission_changed_or_missing")
    history = read_jsonl(staging / "history-private.jsonl")
    roles: dict[str, set] = defaultdict(set)
    historic_identity: dict[tuple[str, str], set] = defaultdict(set)
    for row in history:
        roles[row["writer"]].add(row["split"])
        for key in ("hash", "pixel_hash", "normalized_pixel_hash"):
            historic_identity[key, row[key]].add((row["writer"], row["label"], row["split"]))
    if (
        any(len(value) != 1 for value in roles.values())
        or {k: next(iter(v)) for k, v in roles.items()} != exposure["writers"]
    ):
        raise ValueError("r5_exposure_mapping_disagrees_with_history")
    mappings = {split: read_jsonl(staging / name) for split, name in MAPPINGS.items()}
    if (staging / "train-validation.jsonl").read_bytes() != jsonl(
        [row for split in ("train", "validation") for row in mappings[split]]
    ):
        raise ValueError("r5_training_only_mapping_disagrees_with_frozen_splits")
    geometry_rows = read_jsonl(staging / "image-audit-private.jsonl")
    geometry = {row["id"]: row for row in geometry_rows}
    all_rows = [row for group in mappings.values() for row in group]
    if len(geometry) != len(geometry_rows) or set(geometry) != {r["id"] for r in all_rows}:
        raise ValueError("r5_image_audit_inventory_mismatch")
    if len({r["id"] for r in all_rows}) != len(all_rows):
        raise ValueError("r5_duplicate_mapping_ids")
    observed: dict[tuple[str, str], set] = defaultdict(set)
    unique_images = set()
    per_label_writer = set()
    for split, rows in mappings.items():
        if rows:
            validate_rows(rows, splits={"test" if split == "school_test" else split})
        elif split in {"train", "validation"}:
            raise ValueError("r5_required_training_or_validation_missing")
        if rows and (
            set(r["label"] for r in rows) != set(LETTERS)
            or any(
                sum(r["label"] == label for r in rows)
                < (1 if split == "train" else report["config"]["minimum_evaluation_per_class"])
                for label in LETTERS
            )
        ):
            raise ValueError("r5_letter_coverage_below_frozen_minimum")
        for row in rows:
            parsed = nist.writer_parts(row["source_member"])
            if not parsed:
                raise ValueError("r5_source_member_not_nist_writer_image")
            partition, raw_writer, form, field = parsed
            if (
                row["source_partition"] != partition
                or row["writer"] != nist_writer_id(raw_writer)
                or row["id"] != "r5-" + nist.sha(row["source_member"].encode())[:24]
                or row["source_group"]
                != "nist-page-" + nist.sha((raw_writer + ":" + form).encode())[:24]
                or (field == "u") != row["label"].isupper()
                or field not in {"u", "l"}
            ):
                raise ValueError("r5_nist_provenance_mismatch")
            if (partition == 4) != (split == "school_test"):
                raise ValueError("r5_school_and_primary_domains_mixed")
            old_role = exposure["writers"].get(row["writer"])
            if split in {"train", "validation"}:
                if old_role != split:
                    raise ValueError(
                        "r5_historical_test_or_wrong_writer_role_entered_training_validation"
                    )
            elif old_role is not None:
                raise ValueError("r5_final_test_writer_previously_exposed")
            image_path = checked(staging, row["path"])
            if digest_file(image_path) != row["hash"]:
                raise ValueError("r5_image_bytes_changed")
            with Image.open(image_path) as image:
                identity = image_identity(image)
            if identity["pixel_hash"] != row["pixel_hash"] or geometry[row["id"]] != {
                "id": row["id"],
                **identity,
            }:
                raise ValueError("r5_image_identity_or_geometry_changed")
            norm = identity["normalized_pixel_hash"]
            for key, value in (
                ("hash", row["hash"]),
                ("pixel_hash", row["pixel_hash"]),
                ("normalized_pixel_hash", norm),
            ):
                if (key, value) in unique_images:
                    raise ValueError("r5_selected_duplicate_image")
                unique_images.add((key, value))
                old = historic_identity.get((key, value), set())
                if old and old != {(row["writer"], row["label"], row["split"])}:
                    raise ValueError("r5_historical_duplicate_identity_leakage")
            token = split, row["label"], row["writer"]
            if token in per_label_writer:
                raise ValueError("r5_repeated_writer_for_letter")
            per_label_writer.add(token)
            for key in ("writer", "source_group"):
                observed[key, row[key]].add(split)
    if any(len(value) != 1 for value in observed.values()):
        raise ValueError("r5_cross_split_writer_or_source_leakage")
    selected = {split: mappings[split] for split in ("train", "validation")}
    if include_test:
        selected.update({split: mappings[split] for split in ("test", "school_test")})
    return {
        "status": "r5_data_verified_only",
        "selected": selected,
        "preparation": report,
        "preparation_sha256": digest_file(preparation_path),
        "model_execution": False,
    }


def training_inputs(study: dict) -> TrainingInputs:
    selected = study["selected"]
    return TrainingInputs(
        tuple(cast(Sample, dict(r)) for r in selected["train"]),
        tuple(cast(Sample, dict(r)) for r in selected["validation"]),
        validation_scorer(selected["validation"]),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.staging)
    print(
        json.dumps(
            {
                "status": result["status"],
                "partitions": result["preparation"]["partitions"],
                "model_execution": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
