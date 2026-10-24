"""Score saved English predictions. Pure bookkeeping; never executes a model."""

from __future__ import annotations

from collections import Counter

from research.english.nist import LETTERS
from research.evaluation.local_baselines import recognition_metrics


def validate_rows(truth: list[str], prediction: list[str], writers: list[str]):
    if not truth or not len(truth) == len(prediction) == len(writers):
        raise ValueError("english_metrics_require_equal_nonempty_rows")
    if any(not value for value in truth) or any(not writer for writer in writers):
        raise ValueError("english_metrics_require_labels_and_writer_groups")


def letter_metrics(truth: list[str], prediction: list[str], writers: list[str]) -> dict:
    validate_rows(truth, prediction, writers)
    if any(label not in LETTERS or len(label) != 1 for label in truth):
        raise ValueError("letter_metrics_require_single_ascii_letters")
    result = recognition_metrics(truth, prediction, writers, set(LETTERS))
    result["accuracy"] = sum(a == b for a, b in zip(truth, prediction)) / len(truth)
    result["case_sensitive"] = True
    result["case_only_errors"] = sum(
        a != b and len(b) == 1 and b in LETTERS and a.lower() == b.lower()
        for a, b in zip(truth, prediction)
    )
    result["by_letter"] = {}
    result["by_case"] = {}
    for label in LETTERS:
        indices = [i for i, value in enumerate(truth) if value == label]
        result["by_letter"][label] = {
            "n": len(indices),
            "accuracy": (
                sum(prediction[i] == label for i in indices) / len(indices) if indices else None
            ),
        }
    for case, uppercase in (("uppercase", True), ("lowercase", False)):
        indices = [i for i, value in enumerate(truth) if value.isupper() == uppercase]
        result["by_case"][case] = {
            "n": len(indices),
            "accuracy": (
                sum(truth[i] == prediction[i] for i in indices) / len(indices) if indices else None
            ),
        }
    confusion = Counter(
        (a, b if len(b) == 1 and b in LETTERS else "<other>") for a, b in zip(truth, prediction)
    )
    result["confusion"] = [
        {"true": a, "predicted": b, "n": n} for (a, b), n in sorted(confusion.items())
    ]
    result["normalization"] = "raw_case_sensitive_no_stripping_or_language_correction"
    return result


def word_metrics(
    truth: list[str], prediction: list[str], writers: list[str], training_vocabulary: set[str]
) -> dict:
    validate_rows(truth, prediction, writers)
    result = recognition_metrics(truth, prediction, writers, training_vocabulary)
    result["exact_word_accuracy"] = 1 - result["wer"]
    result["wer_definition"] = "whole_word_crop_exact_mismatch_fraction"
    result["normalization"] = "raw_unicode_case_sensitive_no_stripping_or_language_correction"
    return result
