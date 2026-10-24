"""R5 sample-row adapter: R4-compatible fields, training splits only.

This module never reads, imports or generates a final-test split. Callers hand it a
mapping; every row must belong to ``train`` or ``validation``. A row labelled ``test``
is rejected rather than ignored, so a training invocation cannot silently receive
final-test data.
"""

from __future__ import annotations

import json
import string
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from research.pretrained.artifacts import ROOT, canonical_hash, digest_file

CLASSES: tuple[str, ...] = tuple(string.ascii_uppercase + string.ascii_lowercase)
SAMPLE_FIELDS: tuple[str, ...] = (
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
TRAINING_SPLITS: tuple[str, ...] = ("train", "validation")
_HEX = set("0123456789abcdef")


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def private_staging(path: Path) -> Path:
    """Raw image staging must stay outside the repository checkout."""
    resolved = Path(path).resolve()
    if resolved == ROOT or resolved.is_relative_to(ROOT):
        raise ValueError("r5_raw_staging_must_be_outside_repository")
    if not resolved.is_dir():
        raise ValueError("r5_raw_staging_directory_missing")
    return resolved


def as_mapping(row: object) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    dump = getattr(row, "model_dump", None)
    if callable(dump):
        value = dump()
        if isinstance(value, Mapping):
            return dict(value)
    raise ValueError("r5_sample_row_must_be_a_mapping_or_contract_model")


def _safe_relative(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"r5_sample_{field}_must_be_bounded_text")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value or "\x00" in value:
        raise ValueError(f"r5_sample_{field}_must_be_safe_relative_posix")
    return str(candidate)


def validate_sample(row: object, staging: Path, *, verify_bytes: bool = True) -> dict[str, Any]:
    """Validate one frozen sample row and, optionally, its content-addressed image."""
    value = as_mapping(row)
    if set(value) != set(SAMPLE_FIELDS):
        raise ValueError("r5_sample_fields_differ_from_frozen_contract")
    identity = _safe_relative(value["id"], field="id")
    if not _is_digest(value["hash"]) or not _is_digest(value["pixel_hash"]):
        raise ValueError("r5_sample_hashes_must_be_lowercase_sha256")
    label = value["label"]
    if not isinstance(label, str) or len(label) != 1 or label not in CLASSES:
        raise ValueError("r5_sample_label_outside_case_sensitive_52_classes")
    writer = value["writer"]
    if not isinstance(writer, str) or not writer or len(writer) > 128:
        raise ValueError("r5_sample_writer_must_be_bounded_text")
    group = value["source_group"]
    if not isinstance(group, str) or not group or len(group) > 128:
        raise ValueError("r5_sample_source_group_must_be_bounded_text")
    partition = value["source_partition"]
    if isinstance(partition, bool) or not isinstance(partition, int) or not 0 <= partition <= 999:
        raise ValueError("r5_sample_source_partition_must_be_a_nonnegative_integer")
    split = value["split"]
    if split not in TRAINING_SPLITS:
        # Includes "test": training/evaluation code must never receive final-test rows.
        raise ValueError("r5_sample_split_outside_train_validation")
    member = _safe_relative(value["source_member"], field="source_member")
    path = _safe_relative(value["path"], field="path")
    if path != f"images/{value['hash']}.png":
        raise ValueError("r5_sample_image_reference_not_content_addressed")
    if identity != value["id"]:
        raise ValueError("r5_sample_id_must_not_be_normalized")
    if staging.is_symlink():
        raise ValueError("r5_staging_must_not_be_symlink")
    image_path = staging / path
    if image_path.is_symlink() or not image_path.is_file():
        raise ValueError("r5_sample_image_missing_or_unsafe")
    if not image_path.resolve().is_relative_to(staging):
        raise ValueError("r5_sample_image_escapes_staging")
    if verify_bytes and digest_file(image_path) != value["hash"]:
        raise ValueError("r5_sample_image_content_hash_mismatch")
    return {
        "id": value["id"],
        "hash": value["hash"],
        "path": path,
        "pixel_hash": value["pixel_hash"],
        "label": label,
        "writer": writer,
        "source_group": group,
        "source_partition": partition,
        "split": split,
        "source_member": member,
    }


@dataclass(frozen=True)
class StudyRows:
    """Verified training/validation rows plus the mapping lineage they came from."""

    staging: Path
    train: tuple[dict[str, Any], ...]
    validation: tuple[dict[str, Any], ...]
    mapping_sha256: str | None

    @property
    def splits(self) -> dict[str, tuple[dict[str, Any], ...]]:
        return {"train": self.train, "validation": self.validation}

    def lineage(self) -> dict[str, Any]:
        return {
            "mapping_sha256": self.mapping_sha256,
            "splits": {split: split_lineage(rows) for split, rows in self.splits.items()},
        }


def split_lineage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("r5_split_lineage_requires_rows")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("r5_split_lineage_duplicate_ids")
    labels: dict[str, int] = {}
    for row in rows:
        labels[row["label"]] = labels.get(row["label"], 0) + 1
    return {
        "samples": len(rows),
        "writers": len({row["writer"] for row in rows}),
        "classes": len(labels),
        "per_class": dict(sorted(labels.items())),
        "min_per_class": min(labels.values()),
        "sample_ids_sha256": canonical_hash(sorted(ids)),
        "content_hashes_sha256": canonical_hash(sorted(row["hash"] for row in rows)),
        "pixel_hashes_sha256": canonical_hash(sorted(row["pixel_hash"] for row in rows)),
        "writers_sha256": canonical_hash(sorted({row["writer"] for row in rows})),
    }


def normalize_rows(
    rows: Sequence[object], staging: Path, *, verify_bytes: bool = True
) -> dict[str, list[dict[str, Any]]]:
    """Validate and group caller-supplied rows; reject tests, overlaps and duplicates."""
    staging = private_staging(staging)
    grouped: dict[str, list[dict[str, Any]]] = {"train": [], "validation": []}
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    seen_pixels: set[str] = set()
    for raw in rows:
        row = validate_sample(raw, staging, verify_bytes=verify_bytes)
        if row["id"] in seen_ids:
            raise ValueError("r5_duplicate_sample_id")
        if row["hash"] in seen_hashes:
            raise ValueError("r5_duplicate_content_hash")
        if row["pixel_hash"] in seen_pixels:
            raise ValueError("r5_duplicate_normalized_pixel_hash")
        seen_ids.add(row["id"])
        seen_hashes.add(row["hash"])
        seen_pixels.add(row["pixel_hash"])
        grouped[row["split"]].append(row)
    overlaps = {row["writer"] for row in grouped["train"]} & {
        row["writer"] for row in grouped["validation"]
    }
    if overlaps:
        raise ValueError("r5_train_validation_writer_overlap")
    if not grouped["train"] or not grouped["validation"]:
        raise ValueError("r5_train_and_validation_are_both_required")
    return grouped


def load_mapping(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("r5_mapping_missing_or_unsafe")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"r5_mapping_blank_line:{number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"r5_mapping_invalid_json_line:{number}") from None
        if not isinstance(value, dict):
            raise ValueError(f"r5_mapping_line_not_an_object:{number}")
        rows.append(value)
    if not rows:
        raise ValueError("r5_mapping_empty")
    return rows


def load_study(
    staging: Path,
    mapping_name: str = "study.jsonl",
    *,
    verify_bytes: bool = True,
) -> StudyRows:
    """Load a Part A training mapping. Final-test rows are a hard error."""
    staging = private_staging(staging)
    mapping_path = staging / mapping_name
    rows = load_mapping(mapping_path)
    grouped = normalize_rows(rows, staging, verify_bytes=verify_bytes)
    if any(row["split"] == "test" for row in rows):  # defensive: normalize_rows rejects
        raise ValueError("r5_training_mapping_contains_final_test_rows")
    return StudyRows(
        staging=staging,
        train=tuple(grouped["train"]),
        validation=tuple(grouped["validation"]),
        mapping_sha256=digest_file(mapping_path),
    )
