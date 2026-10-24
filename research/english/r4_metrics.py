"""Case-sensitive R4 letter metrics and writer-clustered confidence intervals."""

from __future__ import annotations

import random
from collections import Counter, defaultdict

from research.english.nist import LETTERS
from research.evaluation.harness import edit_distance


def score_saved_predictions(rows: list[dict], records: list[dict], *, seed: int = 20261022) -> dict:
    """Join a complete prediction inventory to its frozen labels by sample ID."""
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows) or not rows:
        raise ValueError("r4_scoring_requires_unique_nonempty_sample_inventory")
    predictions = {}
    for record in records:
        identity = record.get("sample_id")
        if identity not in by_id or identity in predictions:
            raise ValueError("r4_prediction_unknown_or_duplicate_sample")
        if record.get("truth") != by_id[identity]["label"]:
            raise ValueError("r4_prediction_truth_differs_from_frozen_mapping")
        prediction = record.get("prediction")
        if not isinstance(prediction, str):
            raise ValueError("r4_prediction_must_be_text")
        predictions[identity] = prediction
    if set(predictions) != set(by_id):
        raise ValueError("r4_prediction_inventory_incomplete")
    return letter_metrics(
        [row["label"] for row in rows],
        [predictions[row["id"]] for row in rows],
        [row["writer"] for row in rows],
        seed=seed,
    )


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _writer_bootstrap(
    truth: list[str], prediction: list[str], writers: list[str], *, seed: int, rounds: int
) -> dict:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, writer in enumerate(writers):
        grouped[writer].append(index)
    clusters = sorted(grouped)
    if not clusters:
        raise ValueError("r4_metrics_require_writer_clusters")
    rng = random.Random(seed)
    exact_values, shape_values = [], []
    for _ in range(rounds):
        sampled = [rng.choice(clusters) for _ in clusters]
        indices = [index for writer in sampled for index in grouped[writer]]
        exact_values.append(
            sum(truth[index] == prediction[index] for index in indices) / len(indices)
        )
        shape_values.append(
            sum(
                len(prediction[index]) == 1
                and prediction[index] in LETTERS
                and truth[index].lower() == prediction[index].lower()
                for index in indices
            )
            / len(indices)
        )
    return {
        "method": "writer_cluster_bootstrap_percentile",
        "rounds": rounds,
        "seed": seed,
        "writer_clusters": len(clusters),
        "exact_accuracy_95_percent_ci": [
            _percentile(exact_values, 0.025),
            _percentile(exact_values, 0.975),
        ],
        "casefolded_shape_accuracy_95_percent_ci": [
            _percentile(shape_values, 0.025),
            _percentile(shape_values, 0.975),
        ],
    }


def letter_metrics(
    truth: list[str],
    prediction: list[str],
    writers: list[str],
    *,
    seed: int = 20261022,
    rounds: int = 2000,
) -> dict:
    if not truth or not (len(truth) == len(prediction) == len(writers)):
        raise ValueError("r4_metrics_require_equal_nonempty_rows")
    if any(label not in LETTERS or len(label) != 1 for label in truth):
        raise ValueError("r4_metrics_require_exact_ascii_letter_labels")
    if (
        any(not isinstance(value, str) for value in prediction)
        or any(not writer for writer in writers)
        or not 100 <= rounds <= 20_000
    ):
        raise ValueError("r4_metrics_require_writer_ids_and_bounded_bootstrap")

    correct = [actual == guess for actual, guess in zip(truth, prediction)]
    shape_correct = [
        len(guess) == 1 and guess in LETTERS and actual.lower() == guess.lower()
        for actual, guess in zip(truth, prediction)
    ]
    case_only = [shape and not exact for shape, exact in zip(shape_correct, correct)]
    by_letter = {}
    for label in LETTERS:
        indices = [index for index, value in enumerate(truth) if value == label]
        by_letter[label] = {
            "n": len(indices),
            "correct": sum(correct[index] for index in indices),
            "accuracy": (
                sum(correct[index] for index in indices) / len(indices) if indices else None
            ),
        }

    by_case = {}
    for case, predicate in (("uppercase", str.isupper), ("lowercase", str.islower)):
        indices = [index for index, value in enumerate(truth) if predicate(value)]
        by_case[case] = {
            "n": len(indices),
            "correct": sum(correct[index] for index in indices),
            "accuracy": sum(correct[index] for index in indices) / len(indices),
        }

    per_writer: dict[str, list[int]] = defaultdict(list)
    for index, writer in enumerate(writers):
        per_writer[writer].append(index)
    confusion = Counter(
        (actual, guess if len(guess) == 1 and guess in LETTERS else "<other>")
        for actual, guess in zip(truth, prediction)
    )
    n = len(truth)
    return {
        "n": n,
        "correct": sum(correct),
        "accuracy": sum(correct) / n,
        "exact_case_sensitive": True,
        "case_only_errors": sum(case_only),
        "casefolded_shape_accuracy": sum(shape_correct) / n,
        "scalar_cer": sum(edit_distance(actual, guess) for actual, guess in zip(truth, prediction))
        / n,
        "by_case": by_case,
        "by_letter": by_letter,
        "by_writer": {
            writer: {
                "n": len(indices),
                "correct": sum(correct[index] for index in indices),
                "accuracy": sum(correct[index] for index in indices) / len(indices),
            }
            for writer, indices in sorted(per_writer.items())
        },
        "confusion": [
            {"true": actual, "predicted": guess, "n": count}
            for (actual, guess), count in sorted(confusion.items())
        ],
        "normalization": "raw_exact_case_sensitive_no_trimming_or_language_correction",
        "writer_clustered_confidence_intervals": _writer_bootstrap(
            truth, prediction, writers, seed=seed, rounds=rounds
        ),
    }
