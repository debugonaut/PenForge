"""OWNER-RUN ONLY: English baseline, fixed fine-tune or separate held-out test.

Agents must not invoke this entrypoint, including help/dry runs. Metadata checks
are in research.english.preflight; importing this file never starts a model.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import sys
import time
import zipfile
from pathlib import Path

from research.english.execution import (
    adaptation_reason,
    new_output,
    require_owner,
    validate_parent,
    verify_checkpoint,
)
from research.english.preflight import check
from research.english.study import load_recipe, source_inventory
from research.pretrained.artifacts import ROOT, digest_file, verify_bundle
from research.pretrained.study import tokenizer_for


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["baseline", "finetune", "test"])
    parser.add_argument("--task", choices=["letters", "words"], required=True)
    parser.add_argument("--owner-run", action="store_true")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    require_owner(args.owner_run, args.mode, args.train)
    if args.mode != "baseline" and args.parent is None:
        parser.error("--parent must reference the matching previous report")
    if args.mode == "baseline" and args.parent is not None:
        parser.error("baseline always starts from the original verified model")
    recipe = load_recipe(ROOT / f"research/configs/english-r3-{args.task}.json", args.task)
    os.umask(0o077)
    output = new_output(args.output, (args.model, args.staging))
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    from research.english import runtime as rt

    started, cpu_started = time.monotonic(), time.process_time()
    report = {
        "version": "english-report-r3-v1",
        "status": "running",
        "mode": args.mode,
        "task": args.task,
        "capability_status": "research_only",
        "qualified_for_product": False,
        "font_generation": False,
        "test_evaluated": False,
        "device": "cpu",
    }
    rt.write_json(output / "started.json", report)
    try:
        with rt.Budget(recipe, output):
            study, prepared = check(
                args.staging,
                args.model,
                recipe,
                training=args.mode == "finetune",
                include_test=args.mode == "test",
            )
            lineage = prepared["lineage"]
            parent = None
            parent_hash = None
            if args.parent is not None:
                if args.parent.is_symlink() or not args.parent.is_file():
                    raise ValueError("english_parent_must_be_regular_report")
                parent_hash = digest_file(args.parent)
                parent = json.loads(args.parent.read_text())
                validate_parent(
                    parent, "baseline" if args.mode == "finetune" else "finetune", lineage
                )
                if args.mode == "finetune" and (
                    not args.reason or adaptation_reason(parent) != args.reason
                ):
                    raise ValueError(
                        "english_fitting_requires_recorded_measured_error_justification"
                    )
            report.update(
                lineage=lineage,
                lineage_hash=prepared["lineage_hash"],
                parent_report_sha256=parent_hash,
                python=sys.version,
                platform=platform.platform(),
                original_model_always_used_for_adaptation=True,
                isolated_letters_exploratory=args.task == "letters",
            )
            rt.write_json(output / "inputs-frozen.json", report)
            with zipfile.ZipFile(
                output / "source.zip", "x", compression=zipfile.ZIP_DEFLATED
            ) as snapshot:
                for name in lineage["source_files"]:
                    snapshot.write(ROOT / name, name)
            tokenizer = tokenizer_for(args.model)
            torch = rt.setup(recipe)
            checkpoint = args.model
            if args.mode == "test":
                assert parent is not None and args.parent is not None
                checkpoint = verify_checkpoint(args.parent, parent)
            model = rt.load_model(checkpoint, tokenizer)
            processor = rt.image_processor(args.model)
            if args.mode == "finetune":
                report["owner_justification"] = args.reason
                report["training"] = rt.adapt(
                    torch,
                    model,
                    processor,
                    tokenizer,
                    study["selected"]["train"],
                    args.staging,
                    recipe,
                    output,
                )
                gc.collect()
                model.save_pretrained(
                    output / "checkpoint", safe_serialization=True, max_shard_size="2GB"
                )
                report["checkpoint_files"] = {
                    path.name: digest_file(path)
                    for path in sorted((output / "checkpoint").iterdir())
                }
                verify_checkpoint(output / "report.json", report)
                rt.write_json(output / "checkpoint-frozen-before-test.json", report)
            split = "test" if args.mode == "test" else "validation"
            rows = study["selected"][split]
            metric = rt.evaluate(
                torch,
                model,
                processor,
                tokenizer,
                rows,
                study["vocabulary"],
                args.staging,
                recipe,
                output,
                split,
            )
            constant = sorted(
                study["vocabulary"], key=lambda word: (-study["vocabulary"][word], word)
            )[0]
            report["metrics"] = {
                "pretrained" if args.mode == "baseline" else "adapted": metric,
                "constant_training_label": rt.score(
                    args.task, rows, [constant] * len(rows), study["vocabulary"]
                ),
            }
            report["evaluation_partition"] = lineage["study"]["partitions"][split]
            if args.mode == "finetune":
                assert parent is not None
                before = parent["metrics"]["pretrained"]
                report["validation_comparison"] = {
                    "pretrained_exact_crop_error": before["wer"],
                    "adapted_exact_crop_error": metric["wer"],
                    "pretrained_scalar_cer": before["scalar_cer"],
                    "adapted_scalar_cer": metric["scalar_cer"],
                    "improved_both": metric["wer"] < before["wer"]
                    and metric["scalar_cer"] < before["scalar_cer"],
                    "promotion": "none_regardless_of_improvement",
                }
            if lineage["source_files"] != source_inventory() or (
                args.parent is not None and digest_file(args.parent) != parent_hash
            ):
                raise ValueError("english_code_or_parent_changed_during_execution")
            # Recheck admitted metadata and every selected source after model work.
            _, after = check(
                args.staging,
                args.model,
                recipe,
                training=args.mode == "finetune",
                include_test=args.mode == "test",
            )
            if after["lineage"] != lineage:
                raise ValueError("english_inputs_changed_during_execution")
            if args.mode == "test":
                assert parent is not None and args.parent is not None
                verify_checkpoint(args.parent, parent)
            verify_bundle(args.model)
            report.update(
                status="measured_research_only",
                wall_seconds=time.monotonic() - started,
                cpu_seconds=time.process_time() - cpu_started,
                peak_rss_mb=rt.peak_rss_mb(),
                source_snapshot_sha256=digest_file(output / "source.zip"),
                test_evaluated=args.mode == "test",
                resources_are_observed=True,
                model_inference=True,
            )
            rt.write_json(output / "report.json", report)
            print(
                json.dumps(
                    {
                        "status": report["status"],
                        "task": args.task,
                        "exact_crop_error": metric["wer"],
                        "character_error": metric["scalar_cer"],
                    }
                ),
                flush=True,
            )
    except BaseException as error:
        rt.write_json(
            output / "failure.json",
            {
                "status": "failed",
                "exception_type": type(error).__name__,
                "wall_seconds": time.monotonic() - started,
                "qualified_for_product": False,
            },
        )
        raise SystemExit(
            f"English attempt failed ({type(error).__name__}); inspect the private run."
        ) from None


if __name__ == "__main__":
    main()
