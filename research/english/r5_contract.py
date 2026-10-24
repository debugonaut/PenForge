"""R5 data/model contract v1. Importing this module never imports a model runtime."""

from __future__ import annotations

import re
import string
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict

VERSION = "english-r5-contract-v1"
CLASSES = tuple(string.ascii_uppercase + string.ascii_lowercase)
LETTERS = "".join(CLASSES)
FIELDS = frozenset(
    (
        "id",
        "hash",
        "path",
        "pixel_hash",
        "label",
        "writer",
        "source_group",
        "source_partition",
        "split",
        "source_member",
    )
)


class Sample(TypedDict):
    id: str
    hash: str
    path: str
    pixel_hash: str
    label: str
    writer: str
    source_group: str
    source_partition: int
    split: str
    source_member: str


class Prediction(TypedDict):
    sample_id: str
    truth: str
    prediction: str


ValidationScorer = Callable[[Sequence[Prediction]], dict]


def class_index(label: str) -> int:
    if label not in CLASSES:
        raise ValueError("r5_label_must_be_one_case_sensitive_ascii_letter")
    return CLASSES.index(label)


def validate_rows(rows: Sequence[Mapping], *, splits: set[str]) -> None:
    if not rows:
        raise ValueError("r5_nonempty_sample_inventory_required")
    seen = set()
    for row in rows:
        if set(row) != FIELDS or row["id"] in seen:
            raise ValueError("r5_sample_fields_or_duplicate_id")
        seen.add(row["id"])
        class_index(row["label"])
        if row["split"] not in splits:
            raise ValueError("r5_unexpected_split_in_model_interface")
        if any(
            not isinstance(row[key], str) or not row[key] for key in FIELDS - {"source_partition"}
        ):
            raise ValueError("r5_nonempty_text_fields_required")
        if any(re.fullmatch(r"[0-9a-f]{64}", row[key]) is None for key in ("hash", "pixel_hash")):
            raise ValueError("r5_invalid_content_hash")
        if row["path"] != f"images/{row['hash']}.png":
            raise ValueError("r5_path_must_be_staging_relative_content_addressed_png")
        if type(row["source_partition"]) is not int or row["source_partition"] not in {
            0,
            1,
            2,
            3,
            4,
            6,
            7,
        }:
            raise ValueError("r5_invalid_nist_source_partition")


@dataclass(frozen=True)
class TrainingInputs:
    """Only training/validation rows cross into Part B; final rows are absent."""

    train_rows: tuple[Sample, ...]
    validation_rows: tuple[Sample, ...]
    score_validation: ValidationScorer

    def __post_init__(self):
        validate_rows(self.train_rows, splits={"train"})
        validate_rows(self.validation_rows, splits={"validation"})
        for key in ("id", "writer", "hash", "pixel_hash", "source_group"):
            if {r[key] for r in self.train_rows} & {r[key] for r in self.validation_rows}:
                raise ValueError("r5_training_validation_leakage")
