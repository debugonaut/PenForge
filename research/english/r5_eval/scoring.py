"""Strict ID-joined raw exact-case scoring with writer-cluster uncertainty."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Sequence

from research.english.r5_contract import CLASSES, class_index
from research.evaluation.harness import edit_distance


def _percentile(values, fraction):
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (
        position - lower
    )


def score_predictions(
    rows: Sequence[dict], records: Sequence[dict], *, seed=20261023, rounds=2000
) -> dict:
    by_id = {row["id"]: row for row in rows}
    if not rows or len(by_id) != len(rows):
        raise ValueError("r5_scoring_requires_unique_nonempty_inventory")
    if type(rounds) is not int or not 100 <= rounds <= 20000:
        raise ValueError("r5_bootstrap_rounds_out_of_bounds")
    guesses = {}
    for raw_record in records:
        record = raw_record.model_dump() if hasattr(raw_record, "model_dump") else raw_record
        if not isinstance(record, dict):
            raise ValueError("r5_prediction_record_must_be_mapping_or_contract_model")
        identity = record.get("sample_id")
        if not isinstance(identity, str) or identity not in by_id or identity in guesses:
            raise ValueError("r5_unknown_or_duplicate_prediction_id")
        if record.get("truth") != by_id[identity]["label"]:
            raise ValueError("r5_prediction_truth_mismatch")
        guess = record.get("prediction")
        if not isinstance(guess, str):
            raise ValueError("r5_prediction_must_be_raw_text")
        guesses[identity] = guess
    if guesses.keys() != by_id.keys():
        raise ValueError("r5_missing_prediction_ids")
    letters, writers, cases = defaultdict(list), defaultdict(list), defaultdict(list)
    confusion: Counter[tuple[str, str]] = Counter()
    entries = []
    for row in rows:
        label = row["label"]
        class_index(label)
        if not isinstance(row["writer"], str) or not row["writer"]:
            raise ValueError("r5_writer_id_required")
        guess = guesses[row["id"]]
        exact = label == guess
        folded = guess in CLASSES and label.casefold() == guess.casefold()
        entry = (int(exact), int(folded), int(folded and not exact), edit_distance(label, guess))
        entries.append(entry)
        letters[label].append(entry)
        writers[row["writer"]].append(entry)
        cases["uppercase" if label.isupper() else "lowercase"].append(entry)
        confusion[(label, guess)] += 1

    def summarize(items):
        n = len(items)
        correct = sum(item[0] for item in items)
        return {
            "n": n,
            "correct": correct,
            "accuracy": correct / n if n else None,
            "casefolded_correct": sum(item[1] for item in items),
            "casefolded_accuracy": sum(item[1] for item in items) / n if n else None,
            "case_only_errors": sum(item[2] for item in items),
        }

    clusters = [summarize(writers[writer]) for writer in sorted(writers)]
    rng = random.Random(seed)
    exact_values, shape_values = [], []
    for _ in range(rounds):
        sampled = rng.choices(clusters, k=len(clusters))
        n = sum(item["n"] for item in sampled)
        exact_values.append(sum(item["correct"] for item in sampled) / n)
        shape_values.append(sum(item["casefolded_correct"] for item in sampled) / n)
    return {
        **summarize(entries),
        "exact_case_sensitive": True,
        "casefolded_shape_accuracy": sum(item[1] for item in entries) / len(entries),
        "scalar_cer": sum(item[3] for item in entries) / len(entries),
        "by_letter": {label: summarize(letters[label]) for label in CLASSES},
        "by_case": {case: summarize(cases[case]) for case in ("uppercase", "lowercase")},
        "by_writer": {writer: summarize(items) for writer, items in sorted(writers.items())},
        "writer_macro_accuracy": sum(item["accuracy"] for item in clusters) / len(clusters),
        "confusion": [
            {"truth": label, "prediction": guess, "n": n}
            for (label, guess), n in sorted(confusion.items())
        ],
        "writer_clustered_confidence_intervals": {
            "method": "writer_cluster_bootstrap_percentile",
            "rounds": rounds,
            "seed": seed,
            "writer_clusters": len(clusters),
            "exact_accuracy_95_percent_ci": [_percentile(exact_values, q) for q in (0.025, 0.975)],
            "casefolded_shape_accuracy_95_percent_ci": [
                _percentile(shape_values, q) for q in (0.025, 0.975)
            ],
            "limitation": "Within-sample writer resampling; not uncertainty over corpus or pretraining membership.",
            "degenerate_single_writer": len(clusters) == 1,
        },
        "normalization": "none_raw_exact_no_trimming_no_case_change_no_language_correction",
        "scoring": "complete_unique_sample_id_join_v1",
    }


def validation_scorer(rows: Sequence[dict], **kwargs):
    if any(row["split"] != "validation" for row in rows):
        raise ValueError("r5_validation_callback_must_not_receive_test_or_training_rows")
    frozen = tuple(dict(row) for row in rows)
    return lambda records: score_predictions(frozen, records, **kwargs)
