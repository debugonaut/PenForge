"""R6 row adapter over Part A's shared contract.

Part A owns the row contract (``research/english/r6_contract.py``: the frozen ten R5
fields, the 52-class order, ``validate_rows`` and ``TrainingInputs``). This module does
not define a competing contract: it validates through Part A's function, then adds only
the filesystem checks the model side needs (content-addressed PNGs inside a private
staging root). Any ``split`` other than ``train``/``validation`` is a hard error, so a
test row can never reach the training package.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from research.english.r6_contract import CLASSES, FIELDS, validate_rows
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file

TRAINING_SPLITS: tuple[str, ...] = ("train", "validation")
DEFAULT_MAPPING = "train-validation.jsonl"
_HEX = set("0123456789abcdef")


def private_staging(path: Path) -> Path:
    """Raw image staging must stay outside the repository checkout."""
    resolved = Path(path).resolve()
    if resolved == ROOT or resolved.is_relative_to(ROOT):
        raise ValueError("r6_raw_staging_must_be_outside_repository")
    if not resolved.is_dir():
        raise ValueError("r6_raw_staging_directory_missing")
    return resolved


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _safe_relative(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(f"r6_sample_{field}_must_be_bounded_text")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value or "\x00" in value:
        raise ValueError(f"r6_sample_{field}_must_be_safe_relative_posix")
    return str(candidate)


def as_mapping(row: object) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    dump = getattr(row, "model_dump", None)
    if callable(dump):
        value = dump()
        if isinstance(value, Mapping):
            return dict(value)
    raise ValueError("r6_sample_row_must_be_a_mapping_or_contract_model")


def verify_image_reference(row: Mapping[str, Any], staging: Path) -> None:
    """The row must point at an unchanged content-addressed PNG inside staging."""
    content = row["hash"]
    if not _is_digest(content) or not _is_digest(row["pixel_hash"]):
        raise ValueError("r6_sample_hashes_must_be_lowercase_sha256")
    path = _safe_relative(row["path"], field="path")
    if path != f"images/{content}.png":
        raise ValueError("r6_sample_image_reference_not_content_addressed")
    if staging.is_symlink():
        raise ValueError("r6_staging_must_not_be_symlink")
    target = staging / path
    if target.is_symlink() or not target.is_file():
        raise ValueError("r6_sample_image_missing_or_unsafe")
    if not target.resolve().is_relative_to(staging):
        raise ValueError("r6_sample_image_escapes_staging")
    if digest_file(target) != content:
        raise ValueError("r6_sample_image_content_hash_mismatch")


def load_mapping(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("r6_mapping_missing_or_unsafe")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"r6_mapping_blank_line:{number}")
        import json

        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"r6_mapping_invalid_json_line:{number}") from None
        if not isinstance(value, dict):
            raise ValueError(f"r6_mapping_line_not_an_object:{number}")
        rows.append(value)
    if not rows:
        raise ValueError("r6_mapping_empty")
    return rows


def split_lineage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("r6_split_lineage_requires_rows")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("r6_split_lineage_duplicate_ids")
    labels: dict[str, int] = {}
    for row in rows:
        label = row["label"]
        labels[label] = labels.get(label, 0) + 1
    if set(labels) != set(CLASSES):
        raise ValueError("r6_split_missing_case_sensitive_classes")
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


@dataclass(frozen=True)
class StudyRows:
    """Verified train/validation rows plus the mapping lineage they came from."""

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
            "splits": {name: split_lineage(rows) for name, rows in self.splits.items()},
        }


def normalize_rows(
    rows: Sequence[object], staging: Path, *, verify_bytes: bool = True
) -> dict[str, list[dict[str, Any]]]:
    """Validate caller-supplied rows through Part A's contract and group by split."""
    grouped: dict[str, list[dict[str, Any]]] = {"train": [], "validation": []}
    seen: dict[str, set[str]] = {"id": set(), "hash": set(), "pixel_hash": set()}
    for raw in rows:
        row = as_mapping(raw)
        if set(row) != FIELDS:
            raise ValueError("r6_sample_fields_differ_from_shared_contract")
        normalize = _normalize(row)
        if verify_bytes:
            verify_image_reference(normalize, staging)
        for key, values in seen.items():
            if normalize[key] in values:
                raise ValueError(f"r6_duplicate_sample_{key}")
            values.add(normalize[key])
        grouped[normalize["split"]].append(normalize)
    # Part A's contract rejects wrong splits, duplicate ids and bad field types.
    validate_rows(grouped["train"], splits={"train"})
    validate_rows(grouped["validation"], splits={"validation"})
    for key in ("id", "writer", "hash", "pixel_hash", "source_group"):
        if {row[key] for row in grouped["train"]} & {row[key] for row in grouped["validation"]}:
            raise ValueError("r6_training_validation_leakage")
    return grouped


def _normalize(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "hash": row["hash"],
        "path": _safe_relative(row["path"], field="path"),
        "pixel_hash": row["pixel_hash"],
        "label": row["label"],
        "writer": row["writer"],
        "source_group": row["source_group"],
        "source_partition": row["source_partition"],
        "split": row["split"],
        "source_member": _safe_relative(row["source_member"], field="source_member"),
    }


def load_study(
    staging: Path,
    mapping_name: str = DEFAULT_MAPPING,
    *,
    verify_bytes: bool = True,
) -> StudyRows:
    """Load Part A's training/validation mapping. Test rows are a hard error."""
    root = private_staging(staging)
    mapping_path = root / mapping_name
    rows = load_mapping(mapping_path)
    grouped = normalize_rows(rows, root, verify_bytes=verify_bytes)
    if any(row["split"] == "test" for row in rows):  # defensive: validate_rows rejects
        raise ValueError("r6_training_mapping_contains_test_rows")
    return StudyRows(
        staging=root,
        train=tuple(grouped["train"]),
        validation=tuple(grouped["validation"]),
        mapping_sha256=digest_file(mapping_path),
    )


def study_from_rows(
    train_rows: Sequence[object],
    validation_rows: Sequence[object],
    staging: Path,
    *,
    verify_bytes: bool = True,
    mapping_sha256: str | None = None,
) -> StudyRows:
    """In-memory seam mirroring Part A's ``TrainingInputs`` (no mapping file needed)."""
    root = private_staging(staging)
    combined = [*train_rows, *validation_rows]
    grouped = normalize_rows(combined, root, verify_bytes=verify_bytes)
    if len(grouped["train"]) != len(train_rows) or len(grouped["validation"]) != len(
        validation_rows
    ):
        raise ValueError("r6_in_memory_rows_do_not_match_the_declared_splits")
    return StudyRows(
        staging=root,
        train=tuple(grouped["train"]),
        validation=tuple(grouped["validation"]),
        mapping_sha256=mapping_sha256,
    )
