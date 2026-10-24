"""Boundary checks against Part A's contract and scorer, read-only.

These tests import Part A's modules but never edit or duplicate their rules. They prove
the two halves agree on class order, row fields, prediction records and the one-argument
validation callback. Part A owns those modules; if they are absent the seam is unproven
and the tests skip rather than pass silently.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from research.english.r5_model import evaluate, rows, selection

contract = pytest.importorskip("research.english.r5_contract")
scoring = pytest.importorskip("research.english.r5_eval.scoring")


def png_bytes(salt: int) -> bytes:
    image = Image.new("L", (128, 128), 255)
    image.putpixel((salt % 128, (salt * 7) % 128), 0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def sample(staging: Path, *, label: str, split: str, writer: str, salt: int) -> dict:
    data = png_bytes(salt)
    digest = hashlib.sha256(data).hexdigest()
    (staging / "images").mkdir(parents=True, exist_ok=True)
    (staging / "images" / f"{digest}.png").write_bytes(data)
    with Image.open(io.BytesIO(data)) as image:
        pixels = hashlib.sha256(image.convert("L").tobytes()).hexdigest()
    return {
        "id": f"r5-{label}-{salt}",
        "hash": digest,
        "path": f"images/{digest}.png",
        "pixel_hash": pixels,
        "label": label,
        "writer": writer,
        "source_group": f"nist-page-{salt}",
        "source_partition": 0,
        "split": split,
        "source_member": f"by_write/hsf_0/w{salt}/u{salt}_{salt}.png",
    }


def test_class_order_and_row_fields_match_the_shared_contract():
    assert rows.CLASSES == contract.CLASSES
    assert set(rows.SAMPLE_FIELDS) == set(contract.FIELDS)
    assert list(rows.CLASSES) == [chr(code) for code in range(65, 91)] + [
        chr(code) for code in range(97, 123)
    ]


def test_contract_training_inputs_round_trip_through_the_model_loader(tmp_path):
    staging = tmp_path / "stage"
    staging.mkdir()
    train = [
        sample(staging, label=label, split="train", writer="w-train", salt=index + 1)
        for index, label in enumerate(("A", "b"))
    ]
    validation = [
        sample(staging, label=label, split="validation", writer="w-val", salt=index + 11)
        for index, label in enumerate(("A", "b"))
    ]
    (staging / "train-validation.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in [*train, *validation])
    )
    inputs = contract.TrainingInputs(
        train_rows=tuple(train),
        validation_rows=tuple(validation),
        score_validation=scoring.validation_scorer(validation),
    )
    normalised = rows.normalize_rows([*inputs.train_rows, *inputs.validation_rows], staging)
    assert [row["id"] for row in normalised["train"]] == [row["id"] for row in train]
    loaded = rows.load_study(staging, "train-validation.jsonl")
    assert [row["id"] for row in loaded.train] == [row["id"] for row in train]
    assert [row["id"] for row in loaded.validation] == [row["id"] for row in validation]


def test_part_a_scorer_accepts_part_b_prediction_records(tmp_path):
    staging = tmp_path / "stage"
    staging.mkdir()
    truth = [
        sample(staging, label=label, split="validation", writer="w-val", salt=index + 21)
        for index, label in enumerate(("A", "b"))
    ]
    records = [
        evaluate.PredictionRecord(sample_id=truth[0]["id"], truth="A", prediction="A"),
        evaluate.PredictionRecord(sample_id=truth[1]["id"], truth="b", prediction="B"),
    ]
    score = scoring.validation_scorer(truth)(records)
    accepted = evaluate.validate_score(score, samples=2)
    assert accepted["correct"] == 1
    assert score["exact_case_sensitive"] is True
    assert score["by_letter"]["A"]["correct"] == 1
    assert score["by_letter"]["b"]["correct"] == 0
    assert score["confusion"] == [
        {"truth": "A", "prediction": "A", "n": 1},
        {"truth": "b", "prediction": "B", "n": 1},
    ]

    result = selection.PassResult(
        pass_index=1,
        accuracy=accepted["accuracy"],
        correct=accepted["correct"],
        n=accepted["n"],
        scalar_cer=accepted["scalar_cer"],
        per_crop_ms_p50=1.0,
        exact_case_sensitive=True,
        checkpoint="pass-0001",
        checkpoint_sha256="a" * 64,
        predictions_sha256="b" * 64,
    )
    assert selection.select_best([result])["winner"]["correct"] == 1


def test_part_a_scorer_rejects_training_rows_as_a_validation_callback(tmp_path):
    staging = tmp_path / "stage"
    staging.mkdir()
    train = [sample(staging, label="A", split="train", writer="w", salt=31)]
    with pytest.raises(ValueError, match="must_not_receive"):
        scoring.validation_scorer(train)
