from pathlib import Path

import pytest

from research.evaluation.harness import cer, ece, metrics, paired_error, segmentation
from research.models import BenchmarkRecord
from research.validate import load_record


def test_known_metric_controls_and_empty_human_metrics():
    assert cer([("cat", "cut")]) == 1 / 3
    assert cer([]) is None
    assert ece([(0.9, True), (0.1, False)]) == pytest.approx(0.1)
    assert segmentation([(0, 0), (1, 0)], [(0, 0)]) == (0.5, 2 / 3)
    assert paired_error([([1, 2], [2, 4])]) == 1.5
    assert metrics([], "writer_fidelity")["blinded-preference"] is None
    assert (
        metrics([{"preferred": True, "blinded": False}], "writer_fidelity")["blinded-preference"]
        is None
    )
    assert (
        metrics([{"corrections": 2, "glyphs_reviewed": 4}], "user_burden")["corrections-per-glyph"]
        == 0.5
    )


def test_planned_record_cannot_become_measured_by_label():
    item = load_record(Path("research/evaluation/benchmarks/latn-print-recognition-v1.yaml"))
    with pytest.raises(ValueError):
        BenchmarkRecord.model_validate(item.model_dump() | {"status": "measured"})


def test_metric_counts_exclude_missing_and_unreviewed_observations():
    from research.evaluation.harness import observation_count

    rows = [
        {"truth": "a", "prediction": "b", "stage_ms": 10, "glyphs_reviewed": 3},
        {"stage_ms": 20, "preferred": True, "blinded": False},
        {"preferred": False, "blinded": True, "rubric_hash": "hash", "reviewer_ref": "reviewer"},
    ]
    assert observation_count("cer", rows) == 1
    assert observation_count("stage-p95-ms", rows) == 2
    assert observation_count("blinded-preference", rows) == 1
    assert observation_count("corrections-per-glyph", rows) == 3
