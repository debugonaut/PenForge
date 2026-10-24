"""R6 shared metadata contract. No framework imports or weight deserialization."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

from research.english.r5_contract import (  # immutable, compatible row format
    CLASSES,
    FIELDS,
    LETTERS,
    Prediction,
    Sample,
    class_index,
    validate_rows,
)
from research.english.r5_contract import (
    TrainingInputs as R5TrainingInputs,
)
from research.english.r5_data.common import canonical_hash, checked, digest_file
from research.english.r5_eval.lifecycle import file_inventory

VERSION = "english-r6-contract-v1"
R5_DELTA_SHA256 = "9a39a24f4056f054f1deba42ef972b5a8747401dfd4ea21c21cb5e238aa74e65"
R5_WINNER_SHA256 = "c0ce6b49aba6478c6e35730c43635490046007e6ac4c3265edb7de4b3f83244a"
SELECTION_RULE = "validation_exact_accuracy_then_scalar_cer_then_measured_latency_then_name"


@dataclass(frozen=True)
class TrainingInputs(R5TrainingInputs):
    """The compatible R5 seam, with duplicate identities rejected within each split."""

    def __post_init__(self):
        super().__post_init__()
        rows = (*self.train_rows, *self.validation_rows)
        for key in ("id", "hash", "pixel_hash"):
            if len({row[key] for row in rows}) != len(rows):
                raise ValueError("r6_duplicate_training_validation_identity")


def tensor_header(path: Path) -> dict:
    """Inspect bounded safetensors JSON only, never instantiate tensors."""
    with path.open("rb") as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise ValueError("r6_invalid_tensor_header")
        size = struct.unpack("<Q", prefix)[0]
        if not 2 <= size <= 8_000_000 or size + 8 > path.stat().st_size:
            raise ValueError("r6_invalid_tensor_header_size")
        header = json.loads(stream.read(size))
    payload = path.stat().st_size - size - 8
    offsets = []
    widths = {"F32": 4, "F64": 8, "F16": 2, "BF16": 2, "I64": 8, "I32": 4, "U8": 1}
    for name, item in header.items():
        if name == "__metadata__":
            continue
        start, end = item["data_offsets"]
        shape, dtype = item["shape"], item["dtype"]
        if (
            dtype not in widths
            or any(type(n) is not int or n < 0 for n in shape)
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start <= end <= payload
            or end - start != math.prod(shape) * widths[dtype]
        ):
            raise ValueError("r6_invalid_tensor_inventory")
        offsets.append((start, end))
    ordered = sorted(offsets)
    if (
        not ordered
        or ordered[0][0] != 0
        or ordered[-1][1] != payload
        or any(a[1] != b[0] for a, b in zip(ordered, ordered[1:]))
    ):
        raise ValueError("r6_tensor_payload_inventory_has_gaps_or_overlaps")
    return {name: item for name, item in header.items() if name != "__metadata__"}


@dataclass(frozen=True)
class R5Initialization:
    base_checkpoint: Path
    delta_checkpoint: Path
    winner_file: Path

    def verify(self, *, expected_winner=R5_WINNER_SHA256, expected_delta=R5_DELTA_SHA256) -> dict:
        """Expected hashes are injectable only for inert fixtures/new explicit contracts."""
        if self.winner_file.is_symlink() or digest_file(self.winner_file) != expected_winner:
            raise ValueError("r6_r5_winner_hash_changed")
        winner = json.loads(self.winner_file.read_text())
        lineage = winner["lineage"]
        selected = winner["winner"]
        base = lineage["initialization_adapter"]
        if (
            winner.get("status") != "frozen_before_final_test"
            or winner.get("test_evaluated") is not False
            or winner.get("lineage_hash") != canonical_hash(lineage)
            or selected.get("kind") != "r5_trainable_delta"
            or Path(selected["checkpoint_path"]).resolve() != self.delta_checkpoint.resolve()
            or self.base_checkpoint.resolve() != Path(base["derived_path"]).resolve()
            or file_inventory(self.base_checkpoint) != base["derived_files"]
            or file_inventory(self.delta_checkpoint) != selected["checkpoint_files"]
        ):
            raise ValueError("r6_initialization_must_match_complete_frozen_r5_composition")
        state = json.loads(checked(self.delta_checkpoint, "state.json").read_text())
        tensor = checked(self.delta_checkpoint, "state.safetensors")
        if (
            state.get("version") != "english-r5-selected-state-v1"
            or state.get("status") != "pass_complete"
            or state.get("weights_only") is not True
            or state.get("pass_index") != 3
            or state.get("tensors_sha256") != expected_delta
            or digest_file(tensor) != expected_delta
            or state.get("lineage_hash") != lineage["model_lineage_hash"]
        ):
            raise ValueError("r6_requires_verified_r5_selected_weights_only_state")
        header = tensor_header(tensor)
        inventory = state["trainable_inventory"]
        if set(header) != {"model." + name for name in inventory}:
            raise ValueError("r6_delta_tensor_names_differ_from_trainable_inventory")
        for name, item in inventory.items():
            actual = header["model." + name]
            if actual["shape"] != item["shape"] or actual["dtype"] != "F32":
                raise ValueError("r6_delta_shape_or_dtype_changed")
        return {
            "policy": "verified_r4_base_plus_r5_selected_trainable_delta",
            "winner_sha256": expected_winner,
            "base_checkpoint": str(self.base_checkpoint.resolve()),
            "base_files": file_inventory(self.base_checkpoint),
            "delta_checkpoint": str(self.delta_checkpoint.resolve()),
            "delta_files": file_inventory(self.delta_checkpoint),
            "trainable_inventory": inventory,
            "tensor_header_sha256": canonical_hash(header),
            "optimizer": "new_no_r5_optimizer_in_selected_state",
        }


@dataclass(frozen=True)
class AggregateLimits:
    wall_seconds: int = 16_200
    cpu_seconds: int = 32_400
    rss_mb: int = 6_000
    output_bytes: int = 3_000_000_000
    total_passes: int = 3

    def __post_init__(self):
        ceilings = (16_200, 32_400, 6_000, 3_000_000_000, 3)
        for value, ceiling in zip(self.__dict__.values(), ceilings, strict=True):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError("r6_aggregate_limit_exceeds_owner_envelope")


def rank_key(candidate: dict) -> tuple:
    from research.english.r5_eval.lifecycle import rank_key as strict_rank

    return strict_rank(candidate)


__all__ = [
    "VERSION",
    "CLASSES",
    "FIELDS",
    "LETTERS",
    "Prediction",
    "Sample",
    "TrainingInputs",
    "class_index",
    "validate_rows",
    "R5Initialization",
    "AggregateLimits",
    "rank_key",
]
