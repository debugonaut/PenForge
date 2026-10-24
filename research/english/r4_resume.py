"""Repair R4 validation scoring and continue from its completed classifier-head pass."""

from __future__ import annotations

import argparse
import gc
import json
import os
import time
from pathlib import Path

from research.english.r4_metrics import score_saved_predictions
from research.english.r4_preflight import inspect_inputs
from research.english.r4_recipe import load_recipe
from research.english.r4_run import _candidate
from research.english.r4_runtime import (
    Budget,
    ensure_source_inventory,
    evaluate_classifier,
    load_classifier_checkpoint,
    prepare_output,
    save_classifier_checkpoint,
    setup,
    train_classifier_pass,
    validate_checkpoint_files,
)
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file, verify_bundle
from research.pretrained.runtime import image_processor, load_model, peak_rss_mb
from research.pretrained.study import tokenizer_for


def read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("r4_resume_evidence_missing_or_unsafe")
    return json.loads(path.read_text())


def compatible_lineage(original: dict, current: dict) -> None:
    """Only the scoring repair and this continuation may differ from execution."""
    if {k: v for k, v in original.items() if k != "source_files"} != {
        k: v for k, v in current.items() if k != "source_files"
    }:
        raise ValueError("r4_resume_data_recipe_model_or_environment_changed")
    previous_sources, current_sources = original["source_files"], current["source_files"]
    changed = {
        name
        for name in previous_sources.keys() | current_sources.keys()
        if previous_sources.get(name) != current_sources.get(name)
    }
    if not changed.issubset(
        {
            "research/english/r4_runtime.py",
            "research/english/r4_metrics.py",
            "research/english/r4_resume.py",
        }
    ):
        raise ValueError("r4_resume_unrelated_execution_source_changed")


def repair_report(prior: Path, stage: str, study: dict, preflight: dict, output: Path) -> dict:
    path = prior / stage / "report.json"
    original = read_json(path)
    if (
        original.get("status") != "measured_research_only"
        or original.get("stage") != stage
        or original.get("test_evaluated") is not False
        or original.get("lineage_hash") != canonical_hash(original["lineage"])
        or len(original.get("candidates", [])) != 1
    ):
        raise ValueError("r4_resume_requires_completed_validation_only_report")
    compatible_lineage(original["lineage"], preflight["lineage"])
    candidate = original["candidates"][0]
    expected_kind = "trocr_original" if stage == "baseline" else "trocr_full_pass"
    if candidate.get("kind") != expected_kind or candidate.get("name") != expected_kind:
        raise ValueError("r4_resume_wrong_candidate")
    if stage == "trocr_full_pass":
        training = original.get("training", {})
        n = len(study["selected"]["train"])
        if (
            training.get("status") != "complete_one_pass"
            or training.get("samples") != n
            or training.get("unique_samples") != n
        ):
            raise ValueError("r4_resume_original_training_did_not_complete")
    checkpoint = Path(candidate["checkpoint_path"])
    validate_checkpoint_files(checkpoint, candidate["checkpoint_files"])
    filenames = {
        "baseline": "validation-original-trocr-predictions-private.jsonl",
        "trocr_full_pass": "validation-trocr-full-pass-predictions-private.jsonl",
    }
    predictions = prior / stage / filenames[stage]
    if predictions.is_symlink() or not predictions.is_file():
        raise ValueError("r4_resume_predictions_missing_or_unsafe")
    records = [json.loads(line) for line in predictions.read_text().splitlines()]
    metrics = score_saved_predictions(
        study["selected"]["validation"], records, seed=preflight["lineage"]["recipe"]["seed"]
    )
    for key in ("per_crop_ms_p50", "per_crop_ms_p95"):
        metrics[key] = candidate["validation_metrics"][key]
    metrics["generation_limit_hits"] = None
    metrics["generation_limit_evidence"] = "not_recoverable_from_text_only_prediction_logs"
    repaired = {
        **original,
        "lineage": preflight["lineage"],
        "lineage_hash": preflight["lineage_hash"],
        "lineage_role": "corrected_scoring_and_continuation",
        "execution_lineage": original["lineage"],
        "execution_lineage_hash": original["lineage_hash"],
        "candidates": [{**candidate, "validation_metrics": metrics}],
        "scoring_repair": {
            "method": "complete_sample_id_join_to_frozen_labels_v1",
            "original_report_path": str(path.resolve()),
            "original_report_sha256": digest_file(path),
            "predictions_path": str(predictions.resolve()),
            "predictions_sha256": digest_file(predictions),
            "inference_repeated": False,
            "training_repeated": False,
            "held_out_test_loaded": False,
        },
    }
    folder = output / stage
    folder.mkdir(mode=0o700)
    (folder / "report.json").write_text(
        json.dumps(repaired, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps({"stage": stage, "correct": metrics["correct"], "n": metrics["n"]}), flush=True
    )
    return repaired


def run(args) -> dict:
    if not args.owner_run:
        raise ValueError("r4_resume_requires_explicit_owner_execution_request")
    prior = args.prior_run.resolve()
    if (prior / "fresh-test-claimed.json").exists() or (prior / "final_test").exists():
        raise ValueError("r4_resume_refuses_a_run_that_reached_test")
    recipe = load_recipe()
    study, preflight = inspect_inputs(args.staging, args.model, include_test=False)
    head_stage = prior / "classifier_two_passes"
    original_start = read_json(head_stage / "started.json")
    if original_start.get("stage") != "classifier_two_passes":
        raise ValueError("r4_resume_requires_interrupted_classifier_stage")
    compatible_lineage(original_start["lineage"], preflight["lineage"])
    if (head_stage / "report.json").exists():
        raise ValueError("r4_resume_classifier_already_completed")
    head_path = head_stage / "checkpoints/head-only.safetensors"
    if head_path.is_symlink() or not head_path.is_file():
        raise ValueError("r4_resume_requires_checkpoint_saved_after_complete_head_pass")
    head_hash = digest_file(head_path)
    output = prepare_output(args.output, (args.model, args.staging, prior))
    receipt = {
        "status": "validation_repair_and_classifier_continuation",
        "prior_run": str(prior),
        "prior_classifier_started_sha256": digest_file(head_stage / "started.json"),
        "prior_classifier_execution_lineage": original_start["lineage"],
        "head_checkpoint_sha256": head_hash,
        "head_pass_evidence": "checkpoint_written_only_after_verified_complete_one_pass",
        "lineage": preflight["lineage"],
        "lineage_hash": preflight["lineage_hash"],
        "test_evaluated": False,
    }
    (output / "continuation.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    repair_report(prior, "baseline", study, preflight, output)
    repair_report(prior, "trocr_full_pass", study, preflight, output)
    classifier_output = output / "classifier_two_passes"
    classifier_output.mkdir(mode=0o700)
    started = time.monotonic()
    try:
        torch = setup(recipe)
        tokenizer = tokenizer_for(args.model)
        processor = image_processor(args.model)
        bundle_hash = canonical_hash(preflight["lineage"]["bundle"])
        with Budget(recipe, classifier_output):
            base_model = load_model(args.model, tokenizer)
            encoder = base_model.encoder
            del base_model
            gc.collect()
            head = torch.nn.Linear(encoder.config.hidden_size, 52)
            load_classifier_checkpoint(head_path, encoder, head, include_last_block=False)
            if digest_file(head_path) != head_hash:
                raise ValueError("r4_resume_head_checkpoint_changed")
            head_metrics = evaluate_classifier(
                torch,
                encoder,
                head,
                processor,
                study["selected"]["validation"],
                args.staging,
                recipe,
                classifier_output,
                "validation-classifier-head-only",
            )
        print(
            json.dumps(
                {
                    "stage": "classifier_head_only",
                    "correct": head_metrics["correct"],
                    "n": head_metrics["n"],
                }
            ),
            flush=True,
        )
        head_candidate = _candidate(
            "classifier_head_only", "classifier_head_only", head_path, head_metrics, bundle_hash
        )
        with Budget(recipe, classifier_output):
            final_training = train_classifier_pass(
                torch,
                encoder,
                head,
                processor,
                study["selected"]["train"],
                args.staging,
                recipe,
                phase="final_block",
            )
            final_path = classifier_output / "final-block.safetensors"
            save_classifier_checkpoint(final_path, encoder, head, include_last_block=True)
        with Budget(recipe, classifier_output):
            final_metrics = evaluate_classifier(
                torch,
                encoder,
                head,
                processor,
                study["selected"]["validation"],
                args.staging,
                recipe,
                classifier_output,
                "validation-classifier-final-block",
            )
        ensure_source_inventory(ROOT, preflight["lineage"]["source_files"])
        verify_bundle(args.model)
        n = len(study["selected"]["train"])
        report = {
            "version": "english-r4-report-v1",
            "status": "measured_research_only",
            "stage": "classifier_two_passes",
            "qualified_for_product": False,
            "test_evaluated": False,
            "model_inference": True,
            "lineage": preflight["lineage"],
            "lineage_hash": preflight["lineage_hash"],
            "lineage_role": "corrected_scoring_and_continuation",
            "continuation_evidence": receipt,
            "candidates": [
                head_candidate,
                _candidate(
                    "classifier_final_block",
                    "classifier_final_block",
                    final_path,
                    final_metrics,
                    bundle_hash,
                ),
            ],
            "training": {
                "head_only": {
                    "status": "complete_one_pass",
                    "samples": n,
                    "unique_samples": n,
                    "batches": (n + recipe.classifier_batch_size - 1)
                    // recipe.classifier_batch_size,
                    "encoder_trainable": False,
                    "trainable_scope": "classifier_head",
                    "recovered_from_prior_completed_pass": True,
                    "checkpoint_sha256": head_hash,
                },
                "final_block": final_training,
            },
            "wall_seconds": time.monotonic() - started,
            "peak_rss_mb": peak_rss_mb(),
            "resources_are_observed": True,
            "resources_cover_continuation_only": True,
        }
        (classifier_output / "report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
        print(
            json.dumps(
                {
                    "stage": "classifier_final_block",
                    "correct": final_metrics["correct"],
                    "n": final_metrics["n"],
                    "test_evaluated": False,
                }
            ),
            flush=True,
        )
        return report
    except BaseException as error:
        (classifier_output / "failure.json").write_text(
            json.dumps(
                {
                    "status": "failed_or_incomplete",
                    "exception_type": type(error).__name__,
                    "error_detail_private": str(error)[:1000],
                }
            )
            + "\n"
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-run", action="store_true")
    parser.add_argument("--prior-run", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    run(args)


if __name__ == "__main__":
    main()
