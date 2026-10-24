"""Validation-only candidate ordering. No model or checkpoint execution."""

import pytest

from research.english.r4_select import REQUIRED_CANDIDATES, rank_candidates


def test_r4_selection_uses_exact_accuracy_then_cer_then_latency_and_name():
    candidates = [
        {
            "name": name,
            "kind": name,
            "validation_metrics": {
                "accuracy": accuracy,
                "scalar_cer": cer,
                "per_crop_ms_p50": latency,
            },
        }
        for name, accuracy, cer, latency in (
            ("trocr_original", 0.5, 0.2, 50),
            ("trocr_full_pass", 0.7, 0.3, 60),
            ("classifier_head_only", 0.7, 0.2, 70),
            ("classifier_final_block", 0.7, 0.2, 40),
        )
    ]

    ranked = rank_candidates(candidates)

    assert [candidate["name"] for candidate in ranked] == [
        "classifier_final_block",
        "classifier_head_only",
        "trocr_full_pass",
        "trocr_original",
    ]


def test_r4_selection_requires_all_four_prespecified_candidates():
    with pytest.raises(ValueError, match="prespecified_neural_candidate"):
        rank_candidates(
            [
                {
                    "name": name,
                    "validation_metrics": {
                        "accuracy": 0.5,
                        "scalar_cer": 0.5,
                        "per_crop_ms_p50": 1,
                    },
                }
                for name in sorted(REQUIRED_CANDIDATES)[:-1]
            ]
        )
