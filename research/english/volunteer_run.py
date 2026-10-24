"""Private volunteer stages. Explicit owner evaluation/training stages load models.

The owner runs model stages; every other stage is data-only and imports no model framework.
Every stage prints aggregate counts and progress only, never a per-sample prediction.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from research.english.volunteer import frozen as frozen_module
from research.english.volunteer.manifest import EXPECTED_SAMPLES

STAGES = (
    "prepare",
    "verify",
    "evaluate",
    "audit",
    "variants",
    "compare",
    "prepare-adaptation",
    "verify-adaptation",
    "adapt",
    "holdout-prepare",
    "holdout-compare",
    "holdout-verify",
    "holdout-evaluate",
)
OWNER_STAGES = frozenset({"evaluate", "compare", "adapt", "holdout-evaluate"})


def _run_root(value: Path | None) -> Path:
    raw = value or os.environ.get("PENFORGE_VOLUNTEER_RUN")
    if raw is None:
        raise ValueError("volunteer_run_requires_PENFORGE_VOLUNTEER_RUN_or_--run")
    path = Path(raw).expanduser()
    if path.is_symlink():
        raise ValueError("volunteer_run_root_must_not_be_symlink")
    return path


def _print(value: dict) -> None:
    print(json.dumps(value, sort_keys=True, allow_nan=False), flush=True)


def _summary(report: dict) -> dict:
    samples = report["samples"]
    return {
        "status": report["status"],
        "accuracy": samples["accuracy"],
        "correct": samples["correct"],
        "total": samples["total"],
        "uppercase_accuracy": samples["by_case"]["uppercase"]["accuracy"],
        "lowercase_accuracy": samples["by_case"]["lowercase"]["accuracy"],
        "casefolded_shape_accuracy": samples["casefolded_shape_accuracy"],
        "case_only_errors": samples["case_only_errors"],
        "empty_predictions": samples["empty_predictions"],
        "generation_limit_hits": report["timing"]["generation_limit_hits"],
        "per_writer_accuracy": {
            writer: value["accuracy"] for writer, value in samples["by_writer"].items()
        },
        "wall_seconds": round(report["execution"]["wall_seconds"], 3),
        "per_crop_ms_p50": round(report["timing"]["per_crop_ms_p50"], 3),
        "checkpoint_sha256": report["model"]["checkpoint_sha256"],
        "crop_manifest_sha256": report["data"]["crop_manifest_sha256"],
        "product_qualification": "none",
        "training_performed": False,
        "private_sample_results": "written_to_run_directory_only",
    }


def _progress(line: str) -> None:
    print(line, flush=True)


def _pilot_run(args: argparse.Namespace) -> Path:
    return Path(args.pilot_run).expanduser() if args.pilot_run else Path(args.run).expanduser()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--pilot-run", type=Path)
    parser.add_argument("--coordinates", type=Path)
    parser.add_argument("--staging", type=Path)
    parser.add_argument("--adaptation-config", type=Path)
    parser.add_argument("--adaptation-run", type=Path)
    parser.add_argument("--holdout-coordinates", type=Path)
    parser.add_argument("--baseline-predictions", type=Path)
    parser.add_argument("--adapted-predictions", type=Path)
    parser.add_argument("--frozen-run", type=Path, default=frozen_module.FROZEN_RUN)
    parser.add_argument("--owner-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.stage in OWNER_STAGES and not args.owner_run:
        raise ValueError(f"volunteer_{args.stage.replace('-', '_')}_requires_owner_run")
    run = _run_root(args.run)

    if args.stage == "prepare":
        from research.english.volunteer.prepare import prepare

        coordinates = args.coordinates or (
            Path(os.environ["PENFORGE_VOLUNTEER_COORDINATES"]).expanduser()
            if os.environ.get("PENFORGE_VOLUNTEER_COORDINATES")
            else None
        )
        value = prepare(run, coordinates)
        _print({key: value[key] for key in ("status", "run", "included_samples", "files_written")})
        _print(
            {
                "stage": "prepare",
                "expected_samples": EXPECTED_SAMPLES,
                "model_execution": False,
                "training_performed": False,
                "next": "inspect contact sheets, then the owner runs evaluate",
            }
        )
        return

    if args.stage == "verify":
        from research.english.volunteer.evaluate import verify_prepared as verify_pilot_prepared

        frozen = frozen_module.verify_frozen_winner(args.frozen_run)
        _, receipt, manifest = verify_pilot_prepared(run)
        _print(
            {
                "status": "prepared_and_frozen_winner_verified",
                "winner_name": frozen["winner_name"],
                "winner_document_sha256": frozen["winner_document_sha256"],
                "checkpoint_sha256": frozen["checkpoint_sha256"],
                "pass_index": frozen["pass_index"],
                "inference": frozen["inference"],
                "included_samples": len(manifest.samples),
                "expected_samples": EXPECTED_SAMPLES,
                "crop_manifest_sha256": receipt["crop_manifest_sha256"],
                "model_execution": False,
                "training_performed": False,
            }
        )
        return

    if args.stage == "evaluate":
        from research.english.volunteer.evaluate import evaluate

        report = evaluate(run, frozen_run=args.frozen_run, progress=_progress)
        _print(_summary(report))
        return

    if args.stage == "audit":
        from research.english.volunteer.audit import build

        _print(build(run))
        return

    if args.stage == "variants":
        from research.english.volunteer.variants import prepare as prepare_variants

        _print(prepare_variants(_pilot_run(args), run))
        return

    if args.stage == "compare":
        from research.english.volunteer.comparison import evaluate as compare

        comparison = compare(
            run, pilot_run=_pilot_run(args), frozen_run=args.frozen_run, progress=_progress
        )
        from research.english.volunteer.comparison import summary as comparison_summary

        _print(comparison_summary(comparison))
        return

    if args.stage == "prepare-adaptation":
        from research.english.volunteer.adaptation import load_config
        from research.english.volunteer.adaptation import prepare as prepare_adaptation

        if args.staging is None:
            raise ValueError("volunteer_adaptation_requires_--staging")
        _print(
            prepare_adaptation(
                _pilot_run(args),
                args.staging,
                run,
                frozen_run=args.frozen_run,
                config=load_config(args.adaptation_config),
            )
        )
        return

    if args.stage == "verify-adaptation":
        from research.english.volunteer.adaptation import load_config, output_plan, step_plan
        from research.english.volunteer.adaptation import (
            verify_prepared as verify_adaptation_prepared,
        )

        if args.staging is None:
            raise ValueError("volunteer_adaptation_requires_--staging")
        config = load_config(args.adaptation_config)
        root, prepared, rows, study = verify_adaptation_prepared(
            run,
            pilot_run=_pilot_run(args),
            staging=args.staging,
            frozen_run=args.frozen_run,
            config=config,
        )
        steps = step_plan(len(rows), config)
        _print(
            {
                "status": "prepared_and_frozen_winner_verified",
                "run": str(root),
                "candidate": prepared["candidate"],
                "mixture_samples": len(rows),
                "volunteer_samples": prepared["mixture"]["volunteer_samples"],
                "volunteer_share": prepared["mixture"]["volunteer_share"],
                "validation_samples": len(study.validation),
                "forgetting_floor_correct": prepared["validation"]["forgetting_floor_correct"],
                "optimizer_steps_per_pass": steps.optimizer_steps_per_pass,
                "total_optimizer_steps": steps.total_optimizer_steps,
                "output_plan": output_plan(root, prepared, config),
                "model_execution": False,
                "training_performed": False,
            }
        )
        return

    if args.stage == "adapt":
        from research.english.volunteer.adaptation import load_config
        from research.english.volunteer.adaptation import run as run_adaptation
        from research.english.volunteer.adaptation import summary as adaptation_summary

        if args.staging is None:
            raise ValueError("volunteer_adaptation_requires_--staging")
        report = run_adaptation(
            run,
            owner_run=True,
            pilot_run=_pilot_run(args),
            staging=args.staging,
            frozen_run=args.frozen_run,
            config=load_config(args.adaptation_config),
            resume=args.resume,
            progress=_progress,
        )
        _print(adaptation_summary(report))
        return

    if args.stage == "holdout-prepare":
        from research.english.volunteer.holdout import prepare as prepare_holdout

        coordinates = args.holdout_coordinates or (
            Path(os.environ["PENFORGE_VOLUNTEER_HOLDOUT_COORDINATES"]).expanduser()
            if os.environ.get("PENFORGE_VOLUNTEER_HOLDOUT_COORDINATES")
            else None
        )
        if coordinates is None:
            raise ValueError("volunteer_holdout_w06_w09_data_not_available")
        _print(prepare_holdout(coordinates, run))
        return

    if args.stage == "holdout-compare":
        from research.english.volunteer.holdout import compare as compare_holdout
        from research.english.volunteer.holdout import summary as holdout_summary

        if args.baseline_predictions is None or args.adapted_predictions is None:
            raise ValueError("volunteer_holdout_compare_requires_both_prediction_files")
        _print(
            holdout_summary(
                compare_holdout(
                    run,
                    baseline_predictions=args.baseline_predictions,
                    adapted_predictions=args.adapted_predictions,
                )
            )
        )
        return

    if args.stage in {"holdout-verify", "holdout-evaluate"}:
        from research.english.volunteer import holdout_evaluate

        adaptation_run = args.adaptation_run or holdout_evaluate.DEFAULT_ADAPTATION_RUN
        if args.stage == "holdout-verify":
            plan = holdout_evaluate.prepare_plan(
                run, frozen_run=args.frozen_run, adaptation_run=adaptation_run
            )
            _print(
                {
                    "status": plan["status"],
                    "included_samples": plan["included_samples"],
                    "excluded_cases": plan["excluded_cases"],
                    "writers": plan["writers"],
                    "model_execution": False,
                    "training_performed": False,
                }
            )
        else:
            report = holdout_evaluate.run(
                run,
                owner_run=True,
                frozen_run=args.frozen_run,
                adaptation_run=adaptation_run,
                progress=_progress,
            )
            _print(
                {
                    key: value
                    for key, value in report.items()
                    if key
                    not in {
                        "timing",
                        "evaluation_plan_sha256",
                        "comparison_sha256",
                        "baseline_checkpoint_sha256",
                        "adapted_checkpoint_sha256",
                    }
                }
            )
        return

    raise ValueError(f"volunteer_stage_not_implemented:{args.stage}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, FileExistsError, TimeoutError, RuntimeError) as error:
        print(
            json.dumps({"status": "refused", "reason": str(error), "automatic_retry": False}),
            file=sys.stderr,
            flush=True,
        )
        sys.exit(2)
