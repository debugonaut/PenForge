"""Fixed examples for R4 exact-case and writer-bootstrap metrics."""

import pytest

from research.english.r4_metrics import letter_metrics, score_saved_predictions


def test_r4_metrics_distinguish_exact_case_shape_and_wrong_length_predictions():
    result = letter_metrics(
        ["A", "a", "B", "b"], ["A", "A", "c", "bb"], ["w1", "w2", "w3", "w4"], rounds=200
    )
    assert result["accuracy"] == 0.25
    assert result["casefolded_shape_accuracy"] == 0.5
    assert result["case_only_errors"] == 1
    assert result["scalar_cer"] == 0.75
    assert result["by_case"]["uppercase"]["accuracy"] == 0.5
    assert result["by_case"]["lowercase"]["accuracy"] == 0
    assert result["writer_clustered_confidence_intervals"]["writer_clusters"] == 4


def test_r4_writer_bootstrap_is_deterministic_and_reports_full_single_writer_interval():
    args = (["A", "b"], ["A", "A"], ["writer", "writer"])
    left = letter_metrics(*args, seed=11, rounds=150)
    right = letter_metrics(*args, seed=11, rounds=150)
    assert (
        left["writer_clustered_confidence_intervals"]
        == right["writer_clustered_confidence_intervals"]
    )
    assert left["writer_clustered_confidence_intervals"]["exact_accuracy_95_percent_ci"] == [
        0.5,
        0.5,
    ]


@pytest.mark.parametrize(
    "truth,prediction,writers,rounds",
    [
        ([], [], [], 200),
        (["a"], [], ["w"], 200),
        (["é"], ["é"], ["w"], 200),
        (["A"], ["A"], ["w"], 99),
    ],
)
def test_r4_metrics_reject_incomplete_or_out_of_scope_inputs(truth, prediction, writers, rounds):
    with pytest.raises(ValueError):
        letter_metrics(truth, prediction, writers, rounds=rounds)


def test_saved_predictions_join_frozen_labels_by_id_independently_of_record_order():
    rows = [
        {"id": "one", "label": "A", "writer": "first"},
        {"id": "two", "label": "b", "writer": "second"},
    ]
    records = [
        {"sample_id": "two", "truth": "b", "prediction": "b"},
        {"sample_id": "one", "truth": "A", "prediction": "A"},
    ]
    result = score_saved_predictions(rows, records)
    assert result["correct"] == 2
    assert result["accuracy"] == 1
    assert result["by_writer"]["first"]["correct"] == 1


@pytest.mark.parametrize("damage", ["missing", "duplicate", "unknown", "wrong_truth"])
def test_saved_prediction_repair_refuses_untrusted_or_incomplete_records(damage):
    rows = [
        {"id": "one", "label": "A", "writer": "first"},
        {"id": "two", "label": "b", "writer": "second"},
    ]
    records = [
        {"sample_id": "one", "truth": "A", "prediction": "A"},
        {"sample_id": "two", "truth": "b", "prediction": "b"},
    ]
    if damage == "missing":
        records.pop()
    elif damage == "duplicate":
        records.append(records[0])
    elif damage == "unknown":
        records[0]["sample_id"] = "unmapped"
    else:
        records[0]["truth"] = "C"
    with pytest.raises(ValueError):
        score_saved_predictions(rows, records)
