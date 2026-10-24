"""Deterministic validation-only checkpoint selection.

Exact case-sensitive accuracy is primary, because Part A's scoring is authoritative and
case is never normalised. Ties break on scalar CER, then median per-crop latency, then
the earlier pass. The rule is fixed in the recipe before any run starts, so no test
result can influence it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PassResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    pass_index: int = Field(ge=1, le=4)
    accuracy: float = Field(ge=0, le=1)
    correct: int = Field(ge=0)
    n: int = Field(ge=1)
    scalar_cer: float = Field(ge=0)
    per_crop_ms_p50: float | None = Field(default=None, ge=0)
    exact_case_sensitive: bool
    checkpoint: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predictions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _validate(result: PassResult) -> None:
    if not result.exact_case_sensitive:
        raise ValueError("r5_selection_requires_exact_case_sensitive_scoring")
    if result.correct > result.n:
        raise ValueError("r5_selection_correct_count_exceeds_samples")
    if abs(result.accuracy - result.correct / result.n) > 1e-9:
        raise ValueError("r5_selection_accuracy_disagrees_with_correct_count")
    if not math.isfinite(result.scalar_cer) or not math.isfinite(result.accuracy):
        raise ValueError("r5_selection_metric_not_finite")


def rank_key(result: PassResult) -> tuple[float, float, float, int]:
    _validate(result)
    latency = math.inf if result.per_crop_ms_p50 is None else result.per_crop_ms_p50
    return (-result.accuracy, result.scalar_cer, latency, result.pass_index)


def select_best(results: Sequence[PassResult]) -> dict[str, Any]:
    if not results:
        raise ValueError("r5_selection_requires_at_least_one_complete_pass")
    if len({result.n for result in results}) != 1:
        raise ValueError("r5_selection_requires_one_validation_inventory")
    if len({result.pass_index for result in results}) != len(results):
        raise ValueError("r5_selection_duplicate_pass_index")
    ranked = sorted(results, key=rank_key)
    return {
        "rule": (
            "highest_exact_case_sensitive_validation_accuracy_then_lowest_scalar_cer_"
            "then_lowest_median_latency_then_earlier_pass"
        ),
        "winner": ranked[0].model_dump(),
        "ranked_candidates": [result.model_dump() for result in ranked],
        "validation_samples": ranked[0].n,
    }
