"""Aggregate scoring for the private five-writer volunteer pilot.

Pure bookkeeping over saved prediction records: no model, no image and no bootstrap
interval. Five writers are too few for resampling-based uncertainty to be meaningful, so
this module reports counts only and the report says so explicitly. Every metric is
case-sensitive and raw; nothing is trimmed, folded or language-corrected before scoring.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from research.english.volunteer.manifest import (
    EXPECTED_SAMPLES,
    EXPECTED_SAMPLES_PER_WRITER,
    LOWER,
    UPPER,
    WRITERS,
    expected_sample_id,
)

CASE_LABELS = {"upper": "uppercase", "lower": "lowercase"}


def _index(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate the raw prediction inventory before any aggregate is computed."""
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ValueError("volunteer_predictions_must_be_a_sequence")
    if len(records) != EXPECTED_SAMPLES:
        raise ValueError("volunteer_prediction_count_differs_from_expected_inventory")
    expected = {
        expected_sample_id(writer, group, label): (writer, group, label)
        for writer in WRITERS
        for group, letters in (("upper", UPPER), ("lower", LOWER))
        for label in letters
    }
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ValueError("volunteer_prediction_must_be_an_object")
        sample_id = raw.get("sample_id")
        if not isinstance(sample_id, str) or sample_id not in expected:
            raise ValueError("volunteer_prediction_unknown_sample_id")
        if sample_id in seen:
            raise ValueError("volunteer_prediction_duplicate_sample_id")
        seen.add(sample_id)
        writer, group, label = expected[sample_id]
        if raw.get("writer_id") != writer:
            raise ValueError(f"volunteer_prediction_writer_mismatch:{sample_id}")
        if raw.get("truth") != label:
            raise ValueError(f"volunteer_prediction_truth_mismatch:{sample_id}")
        prediction = raw.get("prediction")
        if not isinstance(prediction, str):
            raise ValueError(f"volunteer_prediction_must_be_raw_text:{sample_id}")
        rows.append(
            {
                "sample_id": sample_id,
                "writer_id": writer,
                "group": group,
                "truth": label,
                "prediction": prediction,
            }
        )
    if seen != set(expected):
        raise ValueError("volunteer_prediction_inventory_incomplete")
    return rows


def _fold(value: str) -> str:
    return value.casefold() if len(value) == 1 else value


def _summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    correct = sum(row["truth"] == row["prediction"] for row in rows)
    folded = sum(_fold(row["truth"]) == _fold(row["prediction"]) for row in rows)
    return {
        "samples": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "casefolded_correct": folded,
        "casefolded_shape_accuracy": folded / total if total else None,
        "case_only_errors": folded - correct,
        "empty_predictions": sum(not row["prediction"].strip() for row in rows),
    }


def score(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate the 260 raw prediction records into the pilot report."""
    rows = _index(records)
    overall = _summary(rows)
    by_writer = {
        writer: _summary([row for row in rows if row["writer_id"] == writer]) for writer in WRITERS
    }
    by_case = {
        CASE_LABELS[group]: _summary([row for row in rows if row["group"] == group])
        for group in ("upper", "lower")
    }
    by_letter = {}
    for group, letters in (("upper", UPPER), ("lower", LOWER)):
        for label in letters:
            subset = [row for row in rows if row["truth"] == label]
            if len(subset) != len(WRITERS):
                raise ValueError(f"volunteer_letter_inventory_incomplete:{label}")
            by_letter[label] = _summary(subset)
    confusion = Counter(
        (row["truth"], row["prediction"] if len(row["prediction"]) == 1 else "<other>")
        for row in rows
    )
    return {
        "samples": len(rows),
        "expected_samples": EXPECTED_SAMPLES,
        "samples_per_writer": EXPECTED_SAMPLES_PER_WRITER,
        "accuracy": overall["accuracy"],
        "correct": overall["correct"],
        "total": overall["samples"],
        "casefolded_shape_accuracy": overall["casefolded_shape_accuracy"],
        "case_only_errors": overall["case_only_errors"],
        "empty_predictions": overall["empty_predictions"],
        "by_case": by_case,
        "by_writer": by_writer,
        "by_letter": {label: by_letter[label] for label in UPPER + LOWER},
        "confusion": [
            {"truth": truth, "prediction": prediction, "n": count}
            for (truth, prediction), count in sorted(confusion.items())
        ],
        "accuracy_definition": "exact_case_sensitive_single_letter_match",
        "normalization": "none_raw_exact_no_trimming_no_case_change_no_language_correction",
        "scoring": "complete_unique_sample_id_join_v1",
        "uncertainty": "not_reported_five_writers_too_few_for_resampling_based_uncertainty",
    }


def wrong_predictions(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count errors only, for a bounded terminal summary that keeps samples private."""
    rows = _index(records)
    return {
        "correct": sum(row["truth"] == row["prediction"] for row in rows),
        "total": len(rows),
    }


__all__ = ["score", "wrong_predictions"]
