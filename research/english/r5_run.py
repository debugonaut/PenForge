"""Explicit R5 commands: metadata preflight, control, training, freeze, ONE final test."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("preflight", "baseline", "train", "freeze", "final-test"))
    for name in ("staging", "model", "initialization", "r4-winner", "run"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--owner-run", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    from research.english.r5_eval import execution

    if args.stage in {"baseline", "train", "final-test"}:
        execution.require_execution(args.owner_run)
    ctx = execution.context(
        args.staging,
        args.model,
        args.initialization,
        args.r4_winner,
        args.run,
        include_test=args.stage == "final-test",
    )
    if args.stage == "preflight":
        value = {
            "status": "r5_integrated_preflight_passed",
            "model_execution": False,
            "partitions": ctx["study"]["preparation"]["partitions"],
            "resource_integration": ctx["lineage"]["resource_integration"],
            "session_sha256": execution.digest_file(ctx["run"] / "session.json"),
        }
    elif args.stage == "baseline":
        report = execution.baseline(ctx, owner_run=args.owner_run)
        value = {
            "status": report["status"],
            "accuracy": report["validation_metrics"]["accuracy"],
            "correct": report["validation_metrics"]["correct"],
            "n": report["validation_metrics"]["n"],
            "test_evaluated": False,
        }
    elif args.stage == "train":
        report = execution.train(ctx, owner_run=args.owner_run)
        value = {
            "status": report["status"],
            "selection": report["selection"],
            "test_evaluated": False,
        }
    elif args.stage == "freeze":
        report = execution.freeze_session(ctx)
        value = {
            "status": report["status"],
            "winner": report["winner"]["name"],
            "validation_accuracy": report["winner"]["validation_metrics"]["accuracy"],
            "test_evaluated": False,
        }
    else:
        report = execution.final_test(ctx, owner_run=args.owner_run)
        value = {
            "status": report["status"],
            "winner": report["winner"],
            "groups": {
                name: {
                    "n": item["metrics"]["n"],
                    "correct": item["metrics"]["correct"],
                    "accuracy": item["metrics"]["accuracy"],
                    "casefolded_shape_accuracy": item["metrics"]["casefolded_shape_accuracy"],
                    "writer_clustered_confidence_intervals": item["metrics"][
                        "writer_clustered_confidence_intervals"
                    ],
                }
                for name, item in report["groups"].items()
            },
            "school_test": report["school_test"],
        }
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
