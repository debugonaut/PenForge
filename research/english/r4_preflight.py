"""Metadata-only R4 input and character-encoding check; no model inference."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path

from research.english.nist import LETTERS
from research.english.r4_recipe import load_recipe, recipe_hash
from research.english.r4_runtime import source_code_inventory
from research.english.r4_study import (
    PRIMARY_PARTITIONS,
    SCHOOL_PARTITION,
    validate_staging,
)
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file, verify_bundle
from research.pretrained.study import encode_label, tokenizer_for


def inspect_inputs(staging: Path, model: Path, *, include_test: bool = False) -> tuple[dict, dict]:
    recipe = load_recipe()
    study = validate_staging(staging, training=True, include_test=include_test)
    preparation = study["preparation"]
    if (
        preparation.get("seed") != recipe.seed
        or preparation.get("primary_source_partitions") != list(PRIMARY_PARTITIONS)
        or preparation.get("cross_domain_partition") != SCHOOL_PARTITION
        or preparation.get("per_class_caps")
        != {"train": 64, "validation": 32, "test": 32, "cross_test": 32}
        or preparation.get("minimum_evaluation_per_class") != 16
    ):
        raise ValueError("r4_preparation_does_not_match_pinned_recipe")
    bundle = verify_bundle(model)
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
    )
    tokenizer = tokenizer_for(model)
    encodings = {
        letter: encode_label(tokenizer, letter, recipe.max_label_tokens) for letter in LETTERS
    }
    if len({tuple(value) for value in encodings.values()}) != len(LETTERS):
        raise ValueError("r4_letter_token_encoding_collision")
    lineage = {
        "recipe": recipe.model_dump(),
        "recipe_hash": recipe_hash(recipe),
        "bundle": bundle,
        "preparation_sha256": digest_file(staging / "preparation.json"),
        "preparation_source_files": preparation["source_code_sha256"],
        "partitions": study["partitions"],
        "cross_domain": study["cross_domain"],
        "tokenizer_encoding": {
            "classes": len(encodings),
            "lossless": True,
            "max_tokens": max(map(len, encodings.values())),
        },
        "source_files": source_code_inventory(ROOT),
        "library_versions": {
            name: importlib.metadata.version(name)
            for name in ("torch", "transformers", "safetensors", "tokenizers", "numpy", "pillow")
        },
    }
    report = {
        "status": "r4_inputs_verified_only",
        "lineage": lineage,
        "lineage_hash": canonical_hash(lineage),
        "partitions": study["partitions"],
        "cross_domain": study["cross_domain"],
        "model_inference": False,
        "training": "not_run",
    }
    return study, report


def check(staging: Path, model: Path) -> dict:
    return inspect_inputs(staging, model)[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    try:
        report = check(args.staging, args.model)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        importlib.metadata.PackageNotFoundError,
    ):
        raise SystemExit(
            "R4 inputs are not ready: verify the local source archives, old R3 stage, "
            "approved R4 preparation, model bundle and pinned Python environment. "
            "No model was loaded."
        ) from None
    if args.summary:
        partitions = report["partitions"]
        report = {
            "status": report["status"],
            "train_samples": partitions["train"]["samples"],
            "validation_samples": partitions["validation"]["samples"],
            "primary_test_samples": partitions["test"]["samples"],
            "cross_domain_test_samples": report["cross_domain"]["samples"],
            "model_execution": False,
        }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
