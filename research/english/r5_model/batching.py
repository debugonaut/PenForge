"""Deterministic batching and label padding that cannot reorder rows against labels."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from research.pretrained.artifacts import canonical_hash
from research.pretrained.study import encode_label


def order_key(seed: int, stage: str, epoch: int, sample_id: str) -> str:
    return canonical_hash([seed, stage, epoch, sample_id])


def _groups(
    rows: Sequence[Mapping[str, Any]], size: int, *, seed: int, stage: str, epoch: int
) -> list[list[dict[str, Any]]]:
    if not rows:
        raise ValueError("r5_batching_requires_rows")
    if size < 1:
        raise ValueError("r5_batching_requires_positive_batch_size")
    if epoch < 0:
        raise ValueError("r5_batching_requires_nonnegative_epoch")
    ordered = sorted(
        (dict(row) for row in rows),
        key=lambda row: order_key(seed, stage, epoch, row["id"]),
    )
    groups = [ordered[start : start + size] for start in range(0, len(ordered), size)]
    verify_coverage(rows, groups)
    return groups


def epoch_batches(
    rows: Sequence[Mapping[str, Any]], size: int, seed: int, epoch: int
) -> list[list[dict[str, Any]]]:
    """Training order for exactly one pass; ``epoch`` makes each pass differ."""
    return _groups(rows, size, seed=seed, stage="trocr-train", epoch=epoch)


def inference_batches(
    rows: Sequence[Mapping[str, Any]], size: int, seed: int, stage: str
) -> list[list[dict[str, Any]]]:
    """Stable evaluation order; never mixes validation and anything else."""
    return _groups(rows, size, seed=seed, stage=f"evaluate-{stage}", epoch=0)


def verify_coverage(
    rows: Sequence[Mapping[str, Any]], groups: Sequence[Sequence[Mapping[str, Any]]]
) -> None:
    """Every input row appears exactly once across the returned groups."""
    expected = [row["id"] for row in rows]
    observed = [row["id"] for group in groups for row in group]
    if len(observed) != len(expected) or len(set(observed)) != len(observed):
        raise ValueError("r5_batching_repeated_or_dropped_rows")
    if sorted(observed) != sorted(expected):
        raise ValueError("r5_batching_changed_the_row_inventory")


def padded_label_ids(torch, tokenizer, labels: Sequence[str], maximum: int):
    """Encode labels in the given order and pad shorter encodings with ``-100``.

    The returned tensor keeps one row per label, in order. A caller that shuffles the
    rows must shuffle these labels with them; the loss then scores each position against
    the label of the same row.
    """
    encoded = [encode_label(tokenizer, label, maximum) for label in labels]
    width = max(len(item) for item in encoded)
    values = [item + [-100] * (width - len(item)) for item in encoded]
    return torch.tensor(values, dtype=torch.long)


def expected_labels(groups: Sequence[Sequence[Mapping[str, Any]]]) -> list[str]:
    return [str(row["label"]) for group in groups for row in group]
