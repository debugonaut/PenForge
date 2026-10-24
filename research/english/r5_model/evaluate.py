"""Deterministic validation prediction output and the injected scoring boundary.

R5 does not implement the authoritative metric. Part A scores the prediction inventory
by sample ID against its frozen mapping; this module only guarantees that every emitted
record is unique, complete, order-preserved and carries the frozen truth for its row.
Greedy decoding, no lexicon and no language correction keep case untouched.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from research.english.r5_model import batching as batching_module
from research.english.r5_model import preprocess as preprocess_module
from research.english.r5_model.recipe import R5ModelRecipe

INFERENCE_BATCH_SIZE = 8


class PredictionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    sample_id: str = Field(min_length=1)
    truth: str = Field(min_length=1)
    prediction: str


def records_for(rows: Sequence[Mapping[str, Any]], texts: Sequence[str]) -> list[PredictionRecord]:
    if len(rows) != len(texts):
        raise ValueError("r5_prediction_count_differs_from_row_count")
    records = [
        PredictionRecord(sample_id=row["id"], truth=row["label"], prediction=text)
        for row, text in zip(rows, texts, strict=True)
    ]
    verify_records(records, rows)
    return records


def verify_records(records: Sequence[PredictionRecord], rows: Sequence[Mapping[str, Any]]) -> None:
    """Reject duplicate, missing, unknown or truth-mismatched predictions."""
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows) or not rows:
        raise ValueError("r5_scoring_requires_unique_nonempty_rows")
    observed: set[str] = set()
    for record in records:
        if not isinstance(record, PredictionRecord):
            raise ValueError("r5_prediction_records_must_be_prediction_record_models")
        if record.sample_id in observed:
            raise ValueError("r5_prediction_duplicate_sample_id")
        row = by_id.get(record.sample_id)
        if row is None:
            raise ValueError("r5_prediction_unknown_sample_id")
        if record.truth != row["label"]:
            raise ValueError("r5_prediction_truth_differs_from_frozen_mapping")
        if not isinstance(record.prediction, str):
            raise ValueError("r5_prediction_must_be_text")
        observed.add(record.sample_id)
    if observed != set(by_id):
        raise ValueError("r5_prediction_inventory_incomplete")


def write_predictions(path: Path, records: Sequence[PredictionRecord]) -> str:
    if path.exists() or path.is_symlink():
        raise ValueError("r5_prediction_output_must_be_new")
    with path.open("x", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record.model_dump(), sort_keys=True) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_predictions(path: Path) -> list[PredictionRecord]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("r5_prediction_file_missing_or_unsafe")
    records = [
        PredictionRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        raise ValueError("r5_prediction_file_empty")
    return records


def scorer_arity(scorer: Callable[..., Any]) -> int:
    """Accept a two-argument ``(records, rows)`` or one-argument ``(records)`` scorer."""
    if not callable(scorer):
        raise ValueError("r5_validation_scorer_must_be_callable")
    try:
        parameters = inspect.signature(scorer).parameters.values()
    except (TypeError, ValueError):
        raise ValueError("r5_validation_scorer_signature_unavailable") from None
    positional = [
        item
        for item in parameters
        if item.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
    ]
    if len(positional) not in {1, 2}:
        raise ValueError("r5_validation_scorer_must_accept_records_and_optional_rows")
    return len(positional)


def call_scorer(
    scorer: Callable[..., Mapping[str, Any]],
    records: Sequence[PredictionRecord],
    rows: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    result = scorer(records, rows) if scorer_arity(scorer) == 2 else scorer(records)
    if not isinstance(result, Mapping):
        raise ValueError("r5_validation_scorer_must_return_a_mapping")
    return result


def validate_score(score: Mapping[str, Any], *, samples: int) -> dict[str, Any]:
    """Check the fields R5 selection needs; Part A owns every other metric."""
    accuracy = score.get("accuracy")
    correct = score.get("correct")
    n = score.get("n")
    cer = score.get("scalar_cer")
    exact = score.get("exact_case_sensitive")
    if exact is not True:
        raise ValueError("r5_scorer_must_report_exact_case_sensitive_accuracy")
    if isinstance(accuracy, bool) or not isinstance(accuracy, (int, float)):
        raise ValueError("r5_scorer_accuracy_missing")
    if not math.isfinite(float(accuracy)) or not 0 <= float(accuracy) <= 1:
        raise ValueError("r5_scorer_accuracy_out_of_range")
    if isinstance(correct, bool) or not isinstance(correct, int) or not 0 <= correct <= samples:
        raise ValueError("r5_scorer_correct_count_out_of_range")
    if isinstance(n, bool) or not isinstance(n, int) or n != samples:
        raise ValueError("r5_scorer_sample_count_differs_from_predictions")
    if abs(float(accuracy) - correct / samples) > 1e-9:
        raise ValueError("r5_scorer_accuracy_disagrees_with_correct_count")
    if isinstance(cer, bool) or not isinstance(cer, (int, float)):
        raise ValueError("r5_scorer_scalar_cer_missing")
    if not math.isfinite(float(cer)) or float(cer) < 0:
        raise ValueError("r5_scorer_scalar_cer_out_of_range")
    latency = score.get("per_crop_ms_p50")
    if latency is not None and (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not math.isfinite(float(latency))
        or float(latency) < 0
    ):
        raise ValueError("r5_scorer_latency_out_of_range")
    return {
        "accuracy": float(accuracy),
        "correct": correct,
        "n": n,
        "scalar_cer": float(cer),
        "per_crop_ms_p50": None if latency is None else float(latency),
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def generate_predictions(
    torch,
    model,
    tokenizer,
    processor,
    rows: Sequence[Mapping[str, Any]],
    staging: Path,
    recipe: R5ModelRecipe,
    *,
    stage: str,
) -> tuple[list[PredictionRecord], dict[str, Any]]:
    """Greedy generation in a stable order; no augmentation and no test rows."""
    if recipe.decoding != "greedy_no_lexicon_no_language_correction":
        raise ValueError("r5_decoding_recipe_mismatch")
    if any(row["split"] != "validation" for row in rows):
        raise ValueError("r5_generation_accepts_validation_rows_only")
    model.eval()
    started = time.monotonic()
    texts: list[str] = []
    evaluated: list[dict[str, Any]] = []
    per_crop_ms: list[float] = []
    truncated = 0
    empty = 0
    groups = batching_module.inference_batches(rows, INFERENCE_BATCH_SIZE, recipe.seed, stage)
    for group in groups:
        tick = time.monotonic()
        values = torch.cat(
            [preprocess_module.validation_pixels(processor, staging, row) for row in group],
            dim=0,
        )
        with torch.inference_mode():
            generated = model.generate(
                pixel_values=values,
                do_sample=False,
                num_beams=1,
                max_new_tokens=recipe.max_label_tokens,
                use_cache=True,
            )
        elapsed = (time.monotonic() - tick) * 1000 / len(group)
        for row, ids in zip(group, generated.tolist(), strict=True):
            text = tokenizer.decode(
                ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )
            evaluated.append(row)
            texts.append(text)
            # TrOCR can share the start and EOS token id; only a later EOS ends output.
            truncated += int(tokenizer.eos_token_id not in ids[1:])
            empty += int(not text.strip())
            per_crop_ms.append(elapsed)
    records = records_for(evaluated, texts)
    stats = {
        "samples": len(records),
        "generation_limit_hits": truncated,
        "empty_predictions": empty,
        "per_crop_ms_p50": _percentile(per_crop_ms, 0.5),
        "per_crop_ms_p95": _percentile(per_crop_ms, 0.95),
        "wall_seconds": time.monotonic() - started,
        "normalization": "raw_exact_case_sensitive_no_trimming_or_language_correction",
    }
    return records, stats
