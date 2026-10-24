"""Pure English execution guards. No model imports, prediction or fitting."""

from __future__ import annotations

import math
from pathlib import Path

from research.pretrained.artifacts import ROOT
from research.pretrained.run import verify_checkpoint


def require_owner(owner: bool, mode: str, train: bool):
    if not owner or mode not in {"baseline", "finetune", "test"}:
        raise ValueError("english_owner_execution_acknowledgment_required")
    if (mode == "finetune") != train:
        raise ValueError("english_train_flag_required_only_for_finetune")


def validate_parent(report: dict, mode: str, lineage: dict):
    if (
        report.get("version") != "english-report-r3-v1"
        or report.get("status") != "measured_research_only"
        or report.get("mode") != mode
        or report.get("lineage") != lineage
    ):
        raise ValueError("english_parent_must_match_measured_mode_and_frozen_lineage")
    metric = report.get("metrics", {}).get("pretrained" if mode == "baseline" else "adapted", {})
    if (
        type(metric.get("n")) is not int
        or metric["n"] != lineage["study"]["partitions"]["validation"]["n"]
    ):
        raise ValueError("english_measured_validation_count_mismatch")
    for name in ("wer", "scalar_cer"):
        value = metric.get(name)
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ValueError("english_measured_metrics_missing_or_invalid")
    if metric["wer"] > 1:
        raise ValueError("english_measured_metrics_missing_or_invalid")
    if mode == "finetune" and (
        not report.get("checkpoint_files")
        or report.get("validation_comparison", {}).get("improved_both") is not True
        or metric.get("predictions_with_replacement_character") != 0
        or metric.get("generation_limit_hits") != 0
    ):
        raise ValueError("english_test_requires_improved_valid_frozen_checkpoint")


def adaptation_reason(report: dict) -> str | None:
    lineage = report.get("lineage", {})
    validate_parent(report, "baseline", lineage)
    metric = report["metrics"]["pretrained"]
    if (
        metric.get("predictions_with_replacement_character") != 0
        or metric.get("generation_limit_hits") != 0
    ):
        raise ValueError("english_review_malformed_or_truncated_baseline_before_fitting")
    if metric["wer"] == 0 and metric["scalar_cer"] == 0:
        return None
    return (
        f"Owner-launched bounded English {lineage['recipe']['task']} pilot. "
        f"Measured validation exact-crop error {metric['wer']:.2%}, "
        f"character error {metric['scalar_cer']:.2%}. Compare the fixed final-step "
        "adaptation against these same validation inputs; no product promotion follows."
    )


def new_output(path: Path, protected: tuple[Path, ...]) -> Path:
    if path.is_symlink():
        raise ValueError("english_output_must_not_be_symlink")
    output = path.resolve()
    if output == ROOT or (
        output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "research/runs")
    ):
        raise ValueError("english_outputs_must_be_private_ignored_or_external")
    if output.exists() or any(
        output.is_relative_to(p.resolve()) or p.resolve().is_relative_to(output) for p in protected
    ):
        raise ValueError("english_choose_new_output_outside_model_and_staging")
    output.mkdir(parents=True, mode=0o700)
    return output


__all__ = [
    "adaptation_reason",
    "new_output",
    "require_owner",
    "validate_parent",
    "verify_checkpoint",
]
