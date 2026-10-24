"""Deterministic R6 validation records, scoring bridge and observable evaluation.

The authoritative metric stays with Part A. This module reuses R5's record, scorer-arity
and score-validation helpers unchanged, and reimplements only the generation loop so it can
report progress every 64 samples. Inference settings are identical to R5's selected
evaluation: the pinned processor, greedy decoding, no beam, no lexicon, no case
normalisation, batch 8 and ``max_new_tokens = recipe.max_label_tokens``. Nothing here is
allowed to see a test row.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from research.english.r5_model import batching as r5_batching
from research.english.r5_model.evaluate import (
    PredictionRecord,
    call_scorer,
    records_for,
    scorer_arity,
    validate_score,
    verify_records,
    write_predictions,
)
from research.english.r6_model import preprocess as preprocess_module
from research.english.r6_model.progress import Progress

INFERENCE_BATCH_SIZE = 8
PROGRESS_EVERY_SAMPLES = 64
NORMALIZATION = "raw_exact_case_sensitive_no_trimming_or_language_correction"

__all__ = [
    "INFERENCE_BATCH_SIZE",
    "PredictionRecord",
    "call_scorer",
    "generate_validation_records",
    "percentile",
    "score_records",
    "scorer_arity",
    "validate_score",
    "verify_records",
    "write_predictions",
]


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("r6_percentile_requires_values")
    if not 0 <= quantile <= 1:
        raise ValueError("r6_percentile_quantile_out_of_range")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def generate_validation_records(
    torch,
    model,
    tokenizer,
    processor,
    rows: Sequence[Mapping[str, Any]],
    staging: Path,
    recipe,
    *,
    stage: str,
    progress: Progress | None = None,
    candidate: str | None = None,
) -> tuple[list[PredictionRecord], dict[str, Any]]:
    """Greedy generation in a stable order; no augmentation and no test rows."""
    if recipe.decoding != "greedy_no_lexicon_no_language_correction":
        raise ValueError("r6_decoding_recipe_mismatch")
    if recipe.validation_preprocessing != "pinned_upstream_vit_rgb_resize_384_v1":
        raise ValueError("r6_validation_preprocessing_recipe_mismatch")
    if any(row["split"] != "validation" for row in rows):
        raise ValueError("r6_generation_accepts_validation_rows_only")
    model.eval()
    started = time.monotonic()
    texts: list[str] = []
    evaluated: list[dict[str, Any]] = []
    per_crop_ms: list[float] = []
    truncated = 0
    empty = 0
    total = len(rows)
    groups = r5_batching.inference_batches(rows, INFERENCE_BATCH_SIZE, recipe.seed, stage)
    for group in groups:
        tick = time.monotonic()
        values = torch.cat(
            [preprocess_module.validation_pixels(processor, staging, row) for row in group], dim=0
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
            # TrOCR shares the start and EOS id, so only a later EOS ends output.
            truncated += int(tokenizer.eos_token_id not in ids[1:])
            empty += int(not text.strip())
            per_crop_ms.append(elapsed)
        if progress is not None and (
            len(evaluated) % PROGRESS_EVERY_SAMPLES == 0 or len(evaluated) == total
        ):
            progress.event(
                "r6-validate",
                candidate=candidate,
                completed_samples=len(evaluated),
                total_samples=total,
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
    records = records_for(evaluated, texts)
    stats = {
        "samples": len(records),
        "generation_limit_hits": truncated,
        "empty_predictions": empty,
        "per_crop_ms_p50": percentile(per_crop_ms, 0.5),
        "per_crop_ms_p95": percentile(per_crop_ms, 0.95),
        "wall_seconds": time.monotonic() - started,
        "normalization": NORMALIZATION,
        "inference_batch_size": INFERENCE_BATCH_SIZE,
        "preprocessing": recipe.validation_preprocessing,
        "decoding": recipe.decoding,
        "max_new_tokens": recipe.max_label_tokens,
        "seed": recipe.seed,
    }
    return records, stats


def score_records(
    scorer: Callable[..., Mapping[str, Any]],
    records: Sequence[PredictionRecord],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Call Part A's authoritative scorer and keep only the fields selection needs."""
    if not callable(scorer):
        raise ValueError("r6_validation_scorer_must_be_callable")
    verify_records(records, rows)
    return validate_score(call_scorer(scorer, records, rows), samples=len(records))


def measured_latency(score: Mapping[str, Any], observed: Mapping[str, Any]) -> float:
    """Part A's latency when reported, otherwise the model's observed median."""
    reported = score.get("per_crop_ms_p50")
    latency = reported if reported is not None else observed["per_crop_ms_p50"]
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        raise ValueError("r6_measured_latency_missing")
    if not math.isfinite(float(latency)) or float(latency) < 0:
        raise ValueError("r6_measured_latency_out_of_range")
    return float(latency)
