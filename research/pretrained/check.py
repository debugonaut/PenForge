"""Read-only R2 bundle/data/text readiness. No model weights are loaded."""

import argparse
import json
from pathlib import Path

from research.pretrained.artifacts import ROOT, canonical_hash, verify_bundle
from research.pretrained.study import encoding_report, load_recipe, prepare, tokenizer_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "research/configs/pretrained-r2.json")
    parser.add_argument("--metadata-only", action="store_true")
    parser.add_argument("--report", type=Path, help="Write a new metadata evidence file")
    args = parser.parse_args()
    bundle = verify_bundle(args.model, metadata_only=args.metadata_only)
    recipe = load_recipe(args.config)
    study, metadata = prepare(args.staging, recipe)
    metadata["encoding"] = encoding_report(
        tokenizer_for(args.model), study["selected"], recipe.max_label_tokens
    )
    metadata["bundle"] = bundle
    metadata["weights_verified"] = not args.metadata_only
    metadata.update(training="not_run", feature_extraction=False, model_inference=False)
    metadata["partitions"] = {
        split: {k: v for k, v in values.items() if k != "sample_ids"}
        for split, values in metadata["partitions"].items()
    }
    metadata["bundle_hash"] = canonical_hash(bundle)
    if args.report:
        destination = args.report.resolve()
        if destination.is_relative_to(ROOT) and not destination.is_relative_to(
            ROOT / "research/runs"
        ):
            parser.error("Evidence outputs must be ignored or external")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with destination.open("x") as report:
            report.write(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
