"""R5 scoring regressions use inert predictions only."""

import pytest

from research.english.r5_contract import CLASSES, class_index
from research.english.r5_eval.scoring import score_predictions, validation_scorer


def inventory():
    return [
        {"id": "a", "label": "A", "writer": "one", "split": "validation"},
        {"id": "b", "label": "a", "writer": "two", "split": "validation"},
        {"id": "c", "label": "B", "writer": "two", "split": "validation"},
    ]


def predictions():
    return [
        {"sample_id": "c", "truth": "B", "prediction": " B"},
        {"sample_id": "b", "truth": "a", "prediction": "A"},
        {"sample_id": "a", "truth": "A", "prediction": "A"},
    ]


def test_complete_id_join_preserves_exact_case_and_whitespace_and_writer_membership():
    result = score_predictions(inventory(), predictions(), rounds=150)
    assert result["correct"] == 1
    assert result["casefolded_shape_accuracy"] == 2 / 3
    assert result["case_only_errors"] == 1
    assert result["by_writer"]["one"]["correct"] == 1
    assert result["by_writer"]["two"]["correct"] == 0
    assert result["by_letter"]["a"]["case_only_errors"] == 1
    assert result == score_predictions(list(reversed(inventory())), predictions(), rounds=150)


@pytest.mark.parametrize(
    "damage", ["duplicate", "missing", "unknown", "truth", "nontext", "duplicate_rows"]
)
def test_bad_prediction_inventories_are_rejected(damage):
    rows, records = inventory(), predictions()
    if damage == "duplicate":
        records.append(records[0])
    elif damage == "missing":
        records.pop()
    elif damage == "unknown":
        records[0]["sample_id"] = "other"
    elif damage == "truth":
        records[1]["truth"] = "A"
    elif damage == "nontext":
        records[0]["prediction"] = None
    else:
        rows.append(rows[0])
    with pytest.raises(ValueError):
        score_predictions(rows, records)


def test_single_case_and_single_writer_have_explicit_uncertainty_limit():
    result = score_predictions(inventory()[:1], predictions()[-1:], rounds=100)
    assert result["by_case"]["lowercase"]["accuracy"] is None
    assert result["writer_clustered_confidence_intervals"]["degenerate_single_writer"]
    assert result["writer_clustered_confidence_intervals"]["exact_accuracy_95_percent_ci"] == [1, 1]


def test_validation_callback_copies_labels_and_rejects_test_rows():
    rows = inventory()
    callback = validation_scorer(rows, rounds=100)
    rows[0]["label"] = "Z"
    assert callback(predictions())["correct"] == 1
    for split in ("test", "train"):
        with pytest.raises(ValueError):
            validation_scorer([{**rows[0], "split": split}])


def test_class_order_is_exact_and_never_case_normalized():
    assert "".join(CLASSES) == "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    assert class_index("A") == 0
    assert class_index("a") == 26
    for label in ("", "Aa", "é", " A"):
        with pytest.raises(ValueError):
            class_index(label)


def test_final_prediction_mechanics_keep_ids_truth_and_final_split_with_inert_runtime(tmp_path):
    import hashlib
    from contextlib import nullcontext
    from types import SimpleNamespace

    from PIL import Image

    from research.english.r5_eval.predict import predict_rows

    (tmp_path / "images").mkdir()
    path = tmp_path / "image.png"
    Image.new("L", (128, 128), 255).save(path)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    path.rename(tmp_path / f"images/{checksum}.png")
    rows = [
        {
            "id": "first",
            "label": "A",
            "writer": "w1",
            "split": "test",
            "path": f"images/{checksum}.png",
            "hash": checksum,
        },
        {
            "id": "second",
            "label": "a",
            "writer": "w2",
            "split": "test",
            "path": f"images/{checksum}.png",
            "hash": checksum,
        },
    ]

    class Model:
        def eval(self):
            pass

        def generate(self, **kwargs):
            assert kwargs["do_sample"] is False
            assert kwargs["num_beams"] == 1
            return SimpleNamespace(tolist=lambda: [[2, 4, 2], [2, 4, 2]])

    torch = SimpleNamespace(cat=lambda values, dim: values, inference_mode=nullcontext)

    def processor(**kwargs):
        return SimpleNamespace(pixel_values="inert")

    tokenizer = SimpleNamespace(eos_token_id=2, decode=lambda *args, **kwargs: "A")
    records, observed = predict_rows(
        torch, Model(), processor, tokenizer, rows, tmp_path, split="test"
    )
    assert observed["generation_limit_hits"] == 0
    assert observed["split"] == "test"
    assert all(row["split"] == "test" for row in rows)
    assert {r["sample_id"]: r["truth"] for r in records} == {r["id"]: r["label"] for r in rows}
    assert score_predictions(rows, records, rounds=100)["accuracy"] == 0.5
    with pytest.raises(ValueError, match="declared_validation_or_final_split"):
        predict_rows(torch, Model(), processor, tokenizer, rows, tmp_path, split="validation")
