"""OWNER-RUN ONLY: bounded English-letter R4 model comparisons and one final test."""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import sys
import time
from pathlib import Path

from research.english.r4_preflight import inspect_inputs
from research.english.r4_recipe import load_recipe
from research.english.r4_runtime import (
    Budget,
    checkpoint_files,
    ensure_source_inventory,
    evaluate_classifier,
    evaluate_trocr,
    load_classifier_checkpoint,
    prepare_output,
    save_classifier_checkpoint,
    save_trocr_checkpoint,
    setup,
    train_classifier_pass,
    train_trocr,
    validate_checkpoint_files,
)
from research.english.r4_select import rank_candidates
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file, verify_bundle
from research.pretrained.runtime import image_processor, load_model
from research.pretrained.study import tokenizer_for

KINDS = {
    "trocr_original",
    "trocr_full_pass",
    "classifier_head_only",
    "classifier_final_block",
}


def _candidate(name: str, kind: str, path: Path, metrics: dict, model_bundle_hash: str) -> dict:
    if path.is_dir():
        inventory = checkpoint_files(path)
    elif path.is_file() and not path.is_symlink():
        inventory = {path.name: digest_file(path)}
    else:
        raise ValueError("r4_candidate_checkpoint_missing_or_unsafe")
    return {
        "name": name,
        "kind": kind,
        "checkpoint_path": str(path.resolve()),
        "checkpoint_files": inventory,
        "model_bundle_hash": model_bundle_hash,
        "validation_metrics": metrics,
    }


def _model_for_candidate(torch, tokenizer, model_path: Path, winner: dict):
    kind = winner["kind"]
    checkpoint = Path(winner["checkpoint_path"])
    validate_checkpoint_files(
        checkpoint, winner["checkpoint_files"]
    ) if checkpoint.is_dir() else None
    if checkpoint.is_file():
        if (
            checkpoint.is_symlink()
            or {checkpoint.name: digest_file(checkpoint)} != winner["checkpoint_files"]
        ):
            raise ValueError("r4_classifier_checkpoint_changed")
    if kind == "trocr_original":
        if checkpoint.resolve() != model_path.resolve():
            raise ValueError("r4_original_candidate_must_use_pinned_bundle")
        return ("trocr", load_model(model_path, tokenizer), None, None)
    if kind == "trocr_full_pass":
        return ("trocr", load_model(checkpoint, tokenizer), None, None)
    if kind not in {"classifier_head_only", "classifier_final_block"}:
        raise ValueError("r4_frozen_winner_kind_not_supported")
    model = load_model(model_path, tokenizer)
    encoder = model.encoder
    del model
    gc.collect()
    if encoder.pooler is not None:
        raise ValueError("r4_classifier_must_use_pretrained_cls_without_pooler")
    head = torch.nn.Linear(encoder.config.hidden_size, 52)
    include_last = kind == "classifier_final_block"
    load_classifier_checkpoint(checkpoint, encoder, head, include_last_block=include_last)
    return ("classifier", encoder, head, include_last)


def _validate_winner(path: Path, expected_lineage: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("r4_frozen_winner_missing_or_unsafe")
    winner_file = json.loads(path.read_text())
    if (
        winner_file.get("version") != "english-r4-frozen-winner-v1"
        or winner_file.get("status") != "frozen_before_test"
        or winner_file.get("lineage_hash") != expected_lineage
        or winner_file.get("fresh_test_claimed") is not False
    ):
        raise ValueError("r4_test_requires_current_frozen_winner")
    candidate = winner_file.get("winner", {})
    if candidate.get("kind") not in KINDS:
        raise ValueError("r4_frozen_winner_kind_not_supported")
    source_reports = winner_file.get("source_reports", [])
    if len(source_reports) != 3 or [item.get("stage") for item in source_reports] != [
        "baseline",
        "trocr_full_pass",
        "classifier_two_passes",
    ]:
        raise ValueError("r4_frozen_winner_report_set_changed")
    report_candidates = []
    for item in source_reports:
        report_path = Path(item["path"])
        if (
            report_path.is_symlink()
            or not report_path.is_file()
            or digest_file(report_path) != item["sha256"]
        ):
            raise ValueError("r4_candidate_report_changed_after_winner_freeze")
        report = json.loads(report_path.read_text())
        if report.get("lineage_hash") != expected_lineage or report.get("stage") != item["stage"]:
            raise ValueError("r4_frozen_winner_report_lineage_changed")
        report_candidates.extend(report.get("candidates", []))
    ranked = rank_candidates(report_candidates)
    if candidate != ranked[0] or winner_file.get("ranked_candidates") != ranked:
        raise ValueError("r4_frozen_winner_differs_from_validation_selection")
    return winner_file


def _claim_final_test(winner_path: Path):
    marker = winner_path.with_name("fresh-test-claimed.json")
    value = {
        "status": "one_final_test_session_claimed",
        "frozen_winner_sha256": digest_file(winner_path),
        "claimed_on": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        with marker.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        raise ValueError("r4_fresh_test_already_claimed") from None


def run(args) -> dict:
    if not args.owner_run:
        raise ValueError("r4_model_execution_requires_owner_run_acknowledgment")
    if args.stage == "final_test" and args.winner is None:
        raise ValueError("r4_final_test_requires_frozen_winner_file")
    if args.stage != "final_test" and args.winner is not None:
        raise ValueError("r4_winner_file_only_applies_to_final_test")

    recipe = load_recipe()
    include_test = args.stage == "final_test"
    study, preflight = inspect_inputs(args.staging, args.model, include_test=include_test)
    output = prepare_output(args.output, (args.model, args.staging))
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
    )
    lineage = preflight["lineage"]
    lineage_hash = preflight["lineage_hash"]
    frozen_sources = lineage["source_files"]
    started_wall, started_cpu = time.monotonic(), time.process_time()
    report = {
        "version": "english-r4-report-v1",
        "status": "running",
        "stage": args.stage,
        "task": "letters",
        "capability_status": "research_only",
        "qualified_for_product": False,
        "test_evaluated": False,
        "model_inference": False,
        "lineage_hash": lineage_hash,
        "lineage": lineage,
        "python": sys.version,
        "platform": platform.platform(),
        "device": "cpu",
    }
    with (output / "started.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    try:
        torch = setup(recipe)
        tokenizer = tokenizer_for(args.model)
        processor = image_processor(args.model)
        model_bundle_hash = canonical_hash(preflight["lineage"]["bundle"])
        candidates = []
        budget_phases = {}

        if args.stage == "baseline":
            phase_started = time.monotonic()
            with Budget(recipe, output):
                model = load_model(args.model, tokenizer)
                metric = evaluate_trocr(
                    torch,
                    model,
                    processor,
                    tokenizer,
                    study["selected"]["validation"],
                    args.staging,
                    recipe,
                    output,
                    "validation-original-trocr",
                )
            budget_phases["validation_original_seconds"] = time.monotonic() - phase_started
            candidates.append(
                _candidate(
                    "trocr_original", "trocr_original", args.model, metric, model_bundle_hash
                )
            )
            report.update(candidates=candidates, training="not_run")

        elif args.stage == "trocr_full_pass":
            phase_started = time.monotonic()
            with Budget(recipe, output):
                model = load_model(args.model, tokenizer)
                training = train_trocr(
                    torch,
                    model,
                    processor,
                    tokenizer,
                    study["selected"]["train"],
                    args.staging,
                    recipe,
                    output,
                )
                checkpoint = output / "checkpoint"
                save_trocr_checkpoint(model, checkpoint)
            budget_phases["training_and_checkpoint_seconds"] = time.monotonic() - phase_started
            phase_started = time.monotonic()
            with Budget(recipe, output):
                metric = evaluate_trocr(
                    torch,
                    model,
                    processor,
                    tokenizer,
                    study["selected"]["validation"],
                    args.staging,
                    recipe,
                    output,
                    "validation-trocr-full-pass",
                )
            budget_phases["validation_seconds"] = time.monotonic() - phase_started
            candidates.append(
                _candidate(
                    "trocr_full_pass", "trocr_full_pass", checkpoint, metric, model_bundle_hash
                )
            )
            report.update(candidates=candidates, training=training)

        elif args.stage == "classifier_two_passes":
            phase_started = time.monotonic()
            with Budget(recipe, output):
                base_model = load_model(args.model, tokenizer)
                encoder = base_model.encoder
                del base_model
                gc.collect()
                if encoder.pooler is not None:
                    raise ValueError("r4_classifier_must_use_pretrained_cls_without_pooler")
                head = torch.nn.Linear(encoder.config.hidden_size, 52)
                first_pass = train_classifier_pass(
                    torch,
                    encoder,
                    head,
                    processor,
                    study["selected"]["train"],
                    args.staging,
                    recipe,
                    phase="head_only",
                )
                checkpoints = output / "checkpoints"
                checkpoints.mkdir(mode=0o700)
                head_path = checkpoints / "head-only.safetensors"
                save_classifier_checkpoint(head_path, encoder, head, include_last_block=False)
            budget_phases["classifier_head_training_seconds"] = time.monotonic() - phase_started
            phase_started = time.monotonic()
            with Budget(recipe, output):
                metric = evaluate_classifier(
                    torch,
                    encoder,
                    head,
                    processor,
                    study["selected"]["validation"],
                    args.staging,
                    recipe,
                    output,
                    "validation-classifier-head-only",
                )
            budget_phases["classifier_head_validation_seconds"] = time.monotonic() - phase_started
            candidates.append(
                _candidate(
                    "classifier_head_only",
                    "classifier_head_only",
                    head_path,
                    metric,
                    model_bundle_hash,
                )
            )
            phase_started = time.monotonic()
            with Budget(recipe, output):
                second_pass = train_classifier_pass(
                    torch,
                    encoder,
                    head,
                    processor,
                    study["selected"]["train"],
                    args.staging,
                    recipe,
                    phase="final_block",
                )
                final_path = checkpoints / "final-block.safetensors"
                save_classifier_checkpoint(final_path, encoder, head, include_last_block=True)
            budget_phases["classifier_final_block_training_seconds"] = (
                time.monotonic() - phase_started
            )
            phase_started = time.monotonic()
            with Budget(recipe, output):
                metric = evaluate_classifier(
                    torch,
                    encoder,
                    head,
                    processor,
                    study["selected"]["validation"],
                    args.staging,
                    recipe,
                    output,
                    "validation-classifier-final-block",
                )
            budget_phases["classifier_final_block_validation_seconds"] = (
                time.monotonic() - phase_started
            )
            candidates.append(
                _candidate(
                    "classifier_final_block",
                    "classifier_final_block",
                    final_path,
                    metric,
                    model_bundle_hash,
                )
            )
            report.update(
                candidates=candidates,
                training={"head_only": first_pass, "final_block": second_pass},
            )

        else:
            assert args.winner is not None
            phase_started = time.monotonic()
            with Budget(recipe, output):
                winner_report = _validate_winner(args.winner, lineage_hash)
                winner = winner_report["winner"]
                if winner.get("model_bundle_hash") != model_bundle_hash:
                    raise ValueError("r4_winner_base_model_bundle_changed")
                _claim_final_test(args.winner)
                mode, model_or_encoder, head, include_last = _model_for_candidate(
                    torch, tokenizer, args.model, winner
                )
                results = {}
                test_groups = [("primary_test", study["selected"]["test"])]
                if "cross_test" in study["selected"]:
                    test_groups.append(("cross_domain_test", study["selected"]["cross_test"]))
                for result_name, rows in test_groups:
                    if mode == "trocr":
                        metrics = evaluate_trocr(
                            torch,
                            model_or_encoder,
                            processor,
                            tokenizer,
                            rows,
                            args.staging,
                            recipe,
                            output,
                            result_name,
                        )
                    else:
                        metrics = evaluate_classifier(
                            torch,
                            model_or_encoder,
                            head,
                            processor,
                            rows,
                            args.staging,
                            recipe,
                            output,
                            result_name,
                        )
                    results[result_name] = metrics
            budget_phases["final_test_seconds"] = time.monotonic() - phase_started
            report.update(
                frozen_winner_sha256=digest_file(args.winner),
                winner_name=winner["name"],
                winner_kind=winner["kind"],
                metrics=results,
                test_evaluated=True,
                training="not_run",
            )

        ensure_source_inventory(ROOT, frozen_sources)
        verify_bundle(args.model)
        report.update(
            status="measured_research_only",
            model_inference=True,
            wall_seconds=time.monotonic() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            peak_rss_mb=__import__(
                "research.pretrained.runtime", fromlist=["peak_rss_mb"]
            ).peak_rss_mb(),
            resources_are_observed=True,
            budget_phases=budget_phases,
            phase_budget_seconds=recipe.max_wall_seconds,
        )
        with (output / "report.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "stage": args.stage,
                    "candidate_count": len(candidates),
                    "test_evaluated": report["test_evaluated"],
                }
            ),
            flush=True,
        )
        return report
    except BaseException as error:
        with (output / "failure.json").open("x", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "status": "failed_or_incomplete",
                        "exception_type": type(error).__name__,
                        "error_detail_private": str(error)[:1000],
                        "qualified_for_product": False,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        raise SystemExit(
            f"R4 {args.stage} failed ({type(error).__name__}); inspect the private run."
        ) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stage",
        choices=["baseline", "trocr_full_pass", "classifier_two_passes", "final_test"],
    )
    parser.add_argument("--owner-run", action="store_true")
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--winner", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    run(args)


if __name__ == "__main__":
    main()
