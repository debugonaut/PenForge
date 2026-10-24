"""Prepare an exact, train/validation-only R6 view of immutable admitted R5 data."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import cast

from PIL import Image

from research.english.r5_data.common import (
    canonical_hash,
    checked,
    digest_file,
    external,
    image_identity,
    jsonl,
    read_jsonl,
    write_json,
)
from research.english.r6_contract import CLASSES, Sample, TrainingInputs, validate_rows
from research.english.r6_eval.scoring import validation_scorer

FILES = ("train.jsonl", "validation.jsonl", "train-validation.jsonl")


def verify_original(staging: Path) -> tuple[dict, dict]:
    staging = external(staging)
    prep = json.loads(checked(staging, "preparation.json").read_text())
    admission = json.loads(checked(staging, "admission.json").read_text())
    if admission.get("scope") != "local_noncommercial_isolated_letters_research" or admission.get(
        "preparation_sha256"
    ) != digest_file(staging / "preparation.json"):
        raise ValueError("r6_existing_r5_admission_or_preparation_changed")
    for name, pin in prep["files"].items():
        if digest_file(checked(staging, name)) != pin:
            raise ValueError("r6_original_frozen_metadata_changed")
    for name, pin in prep["source_code_sha256"].items():
        from research.pretrained.artifacts import ROOT

        if digest_file(checked(ROOT, name)) != pin:
            raise ValueError("r6_original_preparation_code_changed")
    exposure = json.loads(checked(staging, "exposure.json").read_text())
    for source in exposure["sources"] + exposure["admissions"]:
        path = Path(source["path"])
        if path.is_symlink() or digest_file(path) != source["sha256"]:
            raise ValueError("r6_historical_exposure_or_admission_changed")
    rows = {
        split: read_jsonl(checked(staging, split + ".jsonl")) for split in ("train", "validation")
    }
    TrainingInputs(
        tuple(cast(list[Sample], rows["train"])),
        tuple(cast(list[Sample], rows["validation"])),
        validation_scorer(rows["validation"]),
    )
    for split, group in rows.items():
        if set(r["label"] for r in group) != set(CLASSES):
            raise ValueError("r6_all_52_classes_required")
        for row in group:
            if exposure["writers"].get(row["writer"]) != split:
                raise ValueError("r6_writer_role_changed")
    if (staging / "train-validation.jsonl").read_bytes() != jsonl(
        rows["train"] + rows["validation"]
    ):
        raise ValueError("r6_union_mapping_changed")
    return prep, rows


def prepare(source: Path, destination: Path) -> dict:
    source, destination = external(source), external(destination)
    if (
        destination.exists()
        or source.is_relative_to(destination)
        or destination.is_relative_to(source)
    ):
        raise ValueError("r6_new_separate_staging_required")
    prep, rows = verify_original(source)
    destination.mkdir(parents=True, mode=0o700)
    (destination / "images").mkdir(mode=0o700)
    for name in FILES:
        shutil.copyfile(checked(source, name), destination / name)
    for row in rows["train"] + rows["validation"]:
        shutil.copyfile(checked(source, row["path"]), destination / row["path"])
    receipt = {
        "version": "english-r6-stage-v1",
        "policy": "exact_r5_train_validation_only",
        "source": str(source),
        "preparation_sha256": digest_file(source / "preparation.json"),
        "mapping_files": {name: digest_file(destination / name) for name in FILES},
        "admission_scope": "existing_nist_noncommercial_isolated_letter_research",
        "partitions": {
            split: {
                "samples": len(group),
                "writers": len({r["writer"] for r in group}),
                "per_letter": dict(Counter(r["label"] for r in group)),
            }
            for split, group in rows.items()
        },
        "test_rows": 0,
        "model_execution": False,
        "source_archives": prep["sources"],
    }
    write_json(destination / "r6-stage.json", receipt)
    for p in destination.rglob("*"):
        p.chmod(0o700 if p.is_dir() else 0o600)
    return check(destination)


def check(staging: Path) -> dict:
    staging = external(staging)
    receipt = json.loads(checked(staging, "r6-stage.json").read_text())
    if receipt.get("version") != "english-r6-stage-v1" or receipt.get("test_rows") != 0:
        raise ValueError("r6_staging_contract_changed")
    source = Path(receipt["source"])
    if digest_file(checked(source, "preparation.json")) != receipt["preparation_sha256"]:
        raise ValueError("r6_staging_source_changed")
    _, originals = verify_original(source)
    if set(p.name for p in staging.iterdir()) != {*FILES, "images", "r6-stage.json"}:
        raise ValueError("r6_training_stage_contains_unexpected_or_test_files")
    rows = {}
    expected_images = set()
    for split in ("train", "validation"):
        path = checked(staging, split + ".jsonl")
        if digest_file(path) != receipt["mapping_files"][path.name]:
            raise ValueError("r6_mapping_pin_changed")
        group = read_jsonl(path)
        validate_rows(group, splits={split})
        if group != originals[split]:
            raise ValueError("r6_split_changed_from_r5")
        for row in group:
            file = checked(staging, row["path"])
            expected_images.add(file.name)
            if digest_file(file) != row["hash"]:
                raise ValueError("r6_image_content_changed")
            with Image.open(file) as image:
                if image_identity(image)["pixel_hash"] != row["pixel_hash"]:
                    raise ValueError("r6_image_pixel_identity_changed")
        rows[split] = group
    if (staging / "images").is_symlink() or set(
        p.name for p in (staging / "images").iterdir()
    ) != expected_images:
        raise ValueError("r6_staging_image_inventory_contains_non_training_data")
    union = checked(staging, "train-validation.jsonl")
    if digest_file(union) != receipt["mapping_files"][union.name] or union.read_bytes() != jsonl(
        rows["train"] + rows["validation"]
    ):
        raise ValueError("r6_training_union_drift")
    inputs = TrainingInputs(
        tuple(cast(list[Sample], rows["train"])),
        tuple(cast(list[Sample], rows["validation"])),
        validation_scorer(rows["validation"]),
    )
    return {
        "status": "r6_data_verified_only",
        "receipt": receipt,
        "receipt_sha256": digest_file(staging / "r6-stage.json"),
        "rows": rows,
        "inputs": inputs,
        "mapping_hash": canonical_hash(receipt["mapping_files"]),
        "model_execution": False,
    }
