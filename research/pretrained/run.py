"""OWNER-RUN ONLY: unchanged TrOCR baseline, bounded fine-tuning or frozen test.

Agents must not invoke this entrypoint, including as a test/help/dry-run command.
Preparation checks live separately in research.pretrained.check.
"""

from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import math
import os
import platform
import sys
import time
import zipfile
from pathlib import Path

from research.pretrained.artifacts import ROOT, canonical_hash, digest_file, verify_bundle
from research.pretrained.study import (
    encoding_report,
    load_recipe,
    prepare,
    source_inventory,
    tokenizer_for,
)


def require_owner(owner_run: bool, mode: str, train: bool):
    if not owner_run:
        raise ValueError("owner_run_acknowledgment_required")
    if (mode == "finetune") != train:
        raise ValueError("train_flag_required_only_for_finetune")


def validate_parent(parent: dict, mode: str, lineage: dict):
    if (
        parent.get("status") != "measured_research_only"
        or parent.get("mode") != mode
        or parent.get("lineage") != lineage
    ):
        raise ValueError("parent_run_not_compatible_with_frozen_inputs_code_or_recipe")
    if mode == "baseline" and not parent.get("metrics", {}).get("pretrained", {}).get("n"):
        raise ValueError("measured_pretrained_baseline_required")
    if mode == "baseline":
        metric = parent["metrics"]["pretrained"]
        if metric["n"] != lineage["study"]["partitions"]["validation"]["n"]:
            raise ValueError("baseline_partition_count_changed")
        for key in ("wer", "scalar_cer", "grapheme_cer"):
            value = metric.get(key)
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError("baseline_metric_missing_or_invalid")
        if metric["wer"] > 1:
            raise ValueError("baseline_wer_out_of_range")
    if mode == "finetune" and not parent.get("checkpoint_files"):
        raise ValueError("frozen_finetuned_checkpoint_required")


def verify_checkpoint(parent_path: Path, report: dict) -> Path:
    path = parent_path.parent / "checkpoint"
    expected = report["checkpoint_files"]
    if set(expected) != {"config.json", "generation_config.json", "model.safetensors"}:
        raise ValueError("unexpected_checkpoint_inventory")
    if path.is_symlink() or {p.name for p in path.iterdir()} != set(expected):
        raise ValueError("checkpoint_files_changed")
    for name, digest in expected.items():
        p = path / name
        if p.is_symlink() or not p.is_file() or digest_file(p) != digest:
            raise ValueError("checkpoint_checksum_mismatch")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["baseline", "finetune", "test"])
    parser.add_argument("--owner-run", action="store_true")
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "research/configs/pretrained-r2.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    require_owner(args.owner_run, args.mode, args.train)
    if args.mode != "baseline" and args.parent is None:
        parser.error("--parent must name the preceding report.json")
    if args.mode == "finetune" and len(args.reason.strip()) < 12:
        parser.error("--reason must record why the observed pretrained baseline needs adaptation")
    recipe = load_recipe(args.config)
    os.umask(0o077)
    output = args.output.resolve()
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "research/runs"):
        raise ValueError("run_outputs_must_be_ignored_or_external")
    if output.exists():
        raise ValueError("run_directory_already_exists_choose_a_new_name")
    if output.is_relative_to(args.model.resolve()) or output.is_relative_to(args.staging.resolve()):
        raise ValueError("run_directory_cannot_modify_model_or_staging")
    output.mkdir(parents=True, mode=0o700)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")
    from research.pretrained import runtime as rt

    started = time.monotonic()
    cpu_started = time.process_time()
    report = {"mode": args.mode, "status": "running", "capability_status": "research_only"}
    rt.write_json(output / "started.json", report)
    try:
        with rt.Budget(recipe, output):
            bundle = verify_bundle(args.model)
            study, data = prepare(args.staging, recipe, include_test=args.mode == "test")
            tokenizer = tokenizer_for(args.model)
            data["encoding"] = encoding_report(
                tokenizer, study["selected"], recipe.max_label_tokens
            )
            train_validation = dict(data)
            train_validation["partitions"] = {
                k: v for k, v in data["partitions"].items() if k != "test"
            }
            train_validation["encoding"] = {
                k: v for k, v in data["encoding"].items() if k != "test"
            }
            code = source_inventory()
            lineage = {"bundle": bundle, "study": train_validation, "source_files": code}
            parent, parent_hash = None, None
            if args.parent is not None:
                parent_hash = digest_file(args.parent)
                parent = json.loads(args.parent.read_text())
                validate_parent(
                    parent, "baseline" if args.mode == "finetune" else "finetune", lineage
                )
            report.update(
                lineage=lineage,
                lineage_hash=canonical_hash(lineage),
                parent_report_sha256=parent_hash,
                pretraining_overlap=recipe.pretraining_overlap,
                test_policy=recipe.test_policy,
                device="cpu",
                python=sys.version.split()[0],
                platform=platform.platform(),
                library_versions={
                    p: importlib.metadata.version(p)
                    for p in (
                        "torch",
                        "transformers",
                        "safetensors",
                        "tokenizers",
                        "numpy",
                        "pillow",
                    )
                },
                font_generation=False,
                writer_fidelity=None,
                qualified_for_product=False,
                test_evaluated=args.mode == "test",
            )
            rt.write_json(output / "inputs-frozen.json", report)
            with zipfile.ZipFile(
                output / "source.zip", "x", compression=zipfile.ZIP_DEFLATED
            ) as snapshot:
                for name in code:
                    snapshot.write(ROOT / name, name)
            torch = rt.setup(recipe)
            checkpoint = args.model
            if args.mode == "test":
                assert parent is not None
                checkpoint = verify_checkpoint(args.parent, parent)
            model = rt.load_model(checkpoint, tokenizer)
            processor = rt.image_processor(args.model)
            vocabulary = study["vocabulary"]
            constant = sorted(vocabulary, key=lambda word: (-vocabulary[word], word))[0]
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
                    p.name: digest_file(p) for p in sorted((output / "checkpoint").iterdir())
                }
                verify_checkpoint(output / "report.json", report)
                rt.write_json(output / "checkpoint-frozen-before-test.json", report)
            split = "test" if args.mode == "test" else "validation"
            rows = study["selected"][split]
            model_metrics = rt.evaluate(
                torch,
                model,
                processor,
                tokenizer,
                rows,
                vocabulary,
                args.staging,
                recipe,
                output,
                split,
            )
            report["evaluation_partition"] = data["partitions"][split]
            report["metrics"] = {
                "pretrained" if args.mode == "baseline" else "adapted": model_metrics,
                "constant_training_word": rt.score(rows, [constant] * len(rows), vocabulary),
            }
            if args.mode == "finetune":
                assert parent is not None
                before = parent["metrics"]["pretrained"]
                report["validation_comparison"] = {
                    "pretrained_wer": before["wer"],
                    "adapted_wer": model_metrics["wer"],
                    "pretrained_scalar_cer": before["scalar_cer"],
                    "adapted_scalar_cer": model_metrics["scalar_cer"],
                    "improved_both": model_metrics["wer"] < before["wer"]
                    and model_metrics["scalar_cer"] < before["scalar_cer"],
                    "promotion": "none_regardless_of_improvement",
                }
            if code != source_inventory() or (
                args.parent and digest_file(args.parent) != parent_hash
            ):
                raise ValueError("source_or_parent_changed_during_run")
            verify_bundle(args.model)
            report.update(
                status="measured_research_only",
                wall_seconds=time.monotonic() - started,
                peak_rss_mb=rt.peak_rss_mb(),
                source_snapshot_sha256=digest_file(output / "source.zip"),
                resources_are_observed=True,
                cpu_seconds=time.process_time() - cpu_started,
            )
            rt.write_json(output / "report.json", report)
            print(
                json.dumps({"status": report["status"], "metrics": report["metrics"]}), flush=True
            )
    except BaseException as error:
        rt.write_json(
            output / "failure.json",
            {
                "status": "failed",
                "exception_type": type(error).__name__,
                "wall_seconds": time.monotonic() - started,
                "peak_rss_mb": rt.peak_rss_mb(),
                "qualified_for_product": False,
            },
        )
        # Do not print arbitrary framework exception text or private sample labels.
        raise SystemExit(
            f"Research attempt failed ({type(error).__name__}); inspect the private run."
        ) from None


if __name__ == "__main__":
    main()
