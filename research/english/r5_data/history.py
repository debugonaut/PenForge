"""Exposure lineage derived from prior mappings, never historical test mistakes."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PIL import Image

from research.english.r4_study import validate_staging
from research.english.r5_data.common import (
    checked,
    digest_file,
    external,
    image_identity,
    read_jsonl,
)
from research.english.study import Recipe, prepare


def collect_history(r3: Path, r4: Path) -> tuple[list[dict], dict]:
    r3, r4 = external(r3), external(r4)
    # Existing admissions/accepted terms are reused, not replaced with new consent demands.
    prepare(r3, Recipe(task="letters"), training=True, include_test=True)
    validate_staging(r4, training=True, include_test=True)
    rows, sources = [], []
    for root, name in ((r3, "study.jsonl"), (r4, "study.jsonl"), (r4, "cross-domain-study.jsonl")):
        path = checked(root, name)
        sources.append({"path": str(path), "sha256": digest_file(path)})
        for row in read_jsonl(path):
            with Image.open(checked(root, row["path"])) as image:
                identity = image_identity(image)
            if identity["pixel_hash"] != row["pixel_hash"]:
                raise ValueError("r5_historical_pixel_hash_changed")
            rows.append({**row, "normalized_pixel_hash": identity["normalized_pixel_hash"]})
    roles: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        roles[row["writer"]].add(row["split"])
    if any(len(value) != 1 for value in roles.values()):
        raise ValueError("r5_historical_writer_split_conflict")
    return rows, {
        "version": "r5-exposure-history-v1",
        "sources": sources,
        "writers": {writer: next(iter(value)) for writer, value in sorted(roles.items())},
        "role_counts": {
            role: sum(value == {role} for value in roles.values())
            for role in ("train", "validation", "test")
        },
        "earlier_execution_scope": "Conservatively treats every selected R3/R4 mapping row as exposed, even if a run did not visit it.",
        "test_predictions_inspected": False,
        "admissions": [
            {"path": str(root / "admission.json"), "sha256": digest_file(root / "admission.json")}
            for root in (r3, r4)
        ],
    }


def assign_writers(writers: dict[str, int], history: dict[str, str]) -> tuple[dict[str, str], dict]:
    """Keep prior training/validation roles; exclude old tests; reserve ALL untouched writers."""
    result = {}
    for writer, partition in writers.items():
        old = history.get(writer)
        if old == "test":
            continue
        if old in {"train", "validation"}:
            result[writer] = old
        elif old is None:
            result[writer] = "school_test" if partition == 4 else "test"
        else:
            raise ValueError("r5_unknown_historical_role")
    return result, {
        "source_writers": len(writers),
        "historical_test_writers_excluded": sum(history.get(w) == "test" for w in writers),
        "never_exposed_writers": sum(w not in history for w in writers),
        "untouched_by_partition": {
            str(p): sum(v == p and w not in history for w, v in writers.items())
            for p in sorted(set(writers.values()))
        },
        "assigned_writers": {
            role: sum(value == role for value in result.values())
            for role in ("train", "validation", "test", "school_test")
        },
    }
