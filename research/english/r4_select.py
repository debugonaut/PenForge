"""Freeze the validation winner before the one fresh R4 test run."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

from research.english.nist import LETTERS
from research.pretrained.artifacts import digest_file

VERSION = "english-r4-frozen-winner-v1"
REQUIRED_CANDIDATES = {
    "trocr_original",
    "trocr_full_pass",
    "classifier_head_only",
    "classifier_final_block",
}


def _candidate_key(candidate: dict) -> tuple[float, float, float, str]:
    metric = candidate["validation_metrics"]
    accuracy = metric.get("accuracy")
    cer = metric.get("scalar_cer")
    latency = metric.get("per_crop_ms_p50")
    if any(type(value) not in {int, float} for value in (accuracy, cer, latency)):
        raise ValueError("r4_candidate_metrics_missing")
    if (
        not all(math.isfinite(value) for value in (accuracy, cer, latency))
        or not 0 <= accuracy <= 1
        or cer < 0
        or latency < 0
    ):
        raise ValueError("r4_candidate_metrics_out_of_range")
    return (-float(accuracy), float(cer), float(latency), candidate["name"])


def rank_candidates(candidates: list[dict]) -> list[dict]:
    if (
        len(candidates) != len(REQUIRED_CANDIDATES)
        or {candidate.get("name") for candidate in candidates} != REQUIRED_CANDIDATES
    ):
        raise ValueError("r4_requires_every_prespecified_neural_candidate")
    expected_kinds = {
        "trocr_original": "trocr_original",
        "trocr_full_pass": "trocr_full_pass",
        "classifier_head_only": "classifier_head_only",
        "classifier_final_block": "classifier_final_block",
    }
    if any(candidate.get("kind") != expected_kinds[candidate["name"]] for candidate in candidates):
        raise ValueError("r4_candidate_name_kind_mismatch")
    return sorted(candidates, key=_candidate_key)


def _read_report(path: Path, stage: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("r4_candidate_report_missing_or_unsafe")
    report = json.loads(path.read_text())
    if report.get("status") != "measured_research_only" or report.get("stage") != stage:
        raise ValueError("r4_candidate_report_incomplete_or_wrong_stage")
    return report


def _file_inventory(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise ValueError("r4_checkpoint_must_not_be_symlink")
    if path.is_file():
        return {path.name: digest_file(path)}
    if path.is_dir():
        result = {}
        for item in sorted(path.iterdir()):
            if item.is_symlink() or not item.is_file():
                raise ValueError("r4_checkpoint_must_contain_regular_files_only")
            result[item.name] = digest_file(item)
        if not result:
            raise ValueError("r4_checkpoint_empty")
        return result
    raise ValueError("r4_checkpoint_missing")


def freeze(baseline_path: Path, trocr_path: Path, classifier_path: Path, output: Path) -> dict:
    reports = [
        _read_report(baseline_path, "baseline"),
        _read_report(trocr_path, "trocr_full_pass"),
        _read_report(classifier_path, "classifier_two_passes"),
    ]
    lineage_hashes = {report.get("lineage_hash") for report in reports}
    if len(lineage_hashes) != 1 or None in lineage_hashes:
        raise ValueError("r4_candidate_reports_must_share_frozen_lineage")
    candidates = [candidate for report in reports for candidate in report.get("candidates", [])]
    ranked = rank_candidates(candidates)
    validation_count = reports[0]["lineage"]["partitions"]["validation"]["samples"]
    train_count = reports[0]["lineage"]["partitions"]["train"]["samples"]
    for candidate in ranked:
        metrics = candidate["validation_metrics"]
        if (
            metrics.get("exact_case_sensitive") is not True
            or metrics.get("n") != validation_count
            or set(metrics.get("by_letter", {})) != set(LETTERS)
            or any(metrics["by_letter"][letter]["n"] < 16 for letter in LETTERS)
        ):
            raise ValueError("r4_candidate_validation_does_not_cover_frozen_52_class_set")
    if reports[1].get("training", {}).get("status") != "complete_one_pass" or (
        reports[1]["training"].get("samples") != train_count
        or reports[1]["training"].get("unique_samples") != train_count
    ):
        raise ValueError("r4_trocr_full_data_pass_incomplete")
    classifier_training = reports[2].get("training", {})
    if any(
        classifier_training.get(phase, {}).get("status") != "complete_one_pass"
        or classifier_training[phase].get("samples") != train_count
        or classifier_training[phase].get("unique_samples") != train_count
        for phase in ("head_only", "final_block")
    ):
        raise ValueError("r4_classifier_full_data_pass_incomplete")
    for candidate in ranked:
        checkpoint_path = Path(candidate["checkpoint_path"])
        if _file_inventory(checkpoint_path) != candidate["checkpoint_files"]:
            raise ValueError("r4_candidate_checkpoint_changed_before_freeze")
    if output.is_symlink() or output.exists():
        raise ValueError("r4_winner_freeze_must_be_new_regular_file")
    winner = ranked[0]
    source_reports = []
    for path, report in zip((baseline_path, trocr_path, classifier_path), reports, strict=True):
        source_reports.append(
            {
                "path": str(path.resolve()),
                "sha256": digest_file(path),
                "stage": report["stage"],
            }
        )
    value = {
        "version": VERSION,
        "status": "frozen_before_test",
        "lineage_hash": lineage_hashes.pop(),
        "selection_rule": "highest_validation_exact_accuracy_then_lowest_cer_then_lowest_p50_latency_then_name",
        "winner": winner,
        "ranked_candidates": ranked,
        "source_reports": source_reports,
        "fresh_test_claimed": False,
        "product_qualification": "none",
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--trocr", type=Path, required=True)
    parser.add_argument("--classifier", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(freeze(args.baseline, args.trocr, args.classifier, args.output), indent=2))


if __name__ == "__main__":
    main()
