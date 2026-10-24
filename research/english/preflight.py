"""Read-only English admission, model-byte and token checks. Never executes a model."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path

from research.english.study import Recipe, load_recipe, prepare, source_inventory
from research.pretrained.artifacts import ROOT, canonical_hash, verify_bundle
from research.pretrained.study import encoding_report, tokenizer_for


def check(staging: Path, model: Path, recipe: Recipe, *, training=False, include_test=False):
    study, metadata = prepare(staging, recipe, training=training, include_test=include_test)
    bundle = verify_bundle(model)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    tokenizer = tokenizer_for(model)
    encoded = encoding_report(tokenizer, study["selected"], recipe.max_label_tokens)
    metadata["encoding"] = {key: value for key, value in encoded.items() if key != "test"}
    # Test metadata is pinned for all modes, but no test inference happens here.
    lineage = {
        "recipe": recipe.model_dump(),
        "bundle": bundle,
        "study": metadata,
        "source_files": source_inventory(),
        "library_versions": {
            name: importlib.metadata.version(name)
            for name in ("torch", "transformers", "safetensors", "tokenizers", "numpy", "pillow")
        },
    }
    return study, {
        "status": "admitted_inputs_verified_only",
        "lineage": lineage,
        "lineage_hash": canonical_hash(lineage),
        "model_inference": False,
        "training": "not_run",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=["letters", "words"], required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    try:
        recipe = load_recipe(ROOT / f"research/configs/english-r3-{args.task}.json", args.task)
        _, report = check(args.staging, args.model, recipe)
    except (OSError, ValueError, KeyError, TypeError, importlib.metadata.PackageNotFoundError):
        raise SystemExit(
            "English inputs are not ready: finish downloads, actual-file audit, reviewed "
            "admission and the pinned environment first. No model was executed."
        ) from None
    if args.summary:
        partitions = report["lineage"]["study"]["partitions"]
        report = {
            "status": report["status"],
            "task": args.task,
            "train_samples": partitions["train"]["n"],
            "validation_samples": partitions["validation"]["n"],
            "test_partition": "reserved_for_separate_owner_run",
            "model_execution": False,
        }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
