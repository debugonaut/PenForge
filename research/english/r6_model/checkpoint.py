"""Recoverable R6 training state, model-only winner, and crash-safe publication.

R5 wrote its resumable state by removing ``latest/`` and then renaming a prepared
``latest.new/`` into place. That window loses the only recoverable state if the process
dies inside it; the winner promotion had the same pattern. R6 rotates by *renaming*, so at
least one complete state always exists:

    write latest.new -> rename latest -> latest.old -> rename latest.new -> latest
                     -> delete latest.old

Renaming does not duplicate bytes, so the publication peak is unchanged: two full states
plus the model-only winner. A stale ``*.old`` left by a crash is reclaimed before the next
write. Every load re-verifies the inventory, hashes, lineage and recorded budget.
"""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research.english.r6_model.budgets import Consumption, publication_peak_bytes
from research.pretrained.artifacts import digest_file

STATE_VERSION = "english-r6-training-state-v2"
SELECTED_VERSION = "english-r6-selected-state-v2"
STATE_FILES = ("state.safetensors", "state.json")
OPTIMIZER_KEYS = ("exp_avg", "exp_avg_sq", "step")
LATEST_FILES = ("latest.new", "latest.old")
SELECTED_FILES = ("selected-candidate", "best.old")


class ParameterRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dtype: str
    shape: list[int]


class PassCommit(BaseModel):
    """One complete pass, committed inside the state so the results view is derivable."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    pass_index: int = Field(ge=1, le=3)
    data_epoch: int = Field(ge=1)
    accuracy: float = Field(ge=0, le=1)
    correct: int = Field(ge=0)
    n: int = Field(ge=1)
    scalar_cer: float = Field(ge=0)
    per_crop_ms_p50: float = Field(ge=0)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    predictions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    samples: int = Field(ge=1)
    optimizer_steps: int = Field(ge=0)
    training_seconds: float = Field(ge=0)
    validation_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "PassCommit":
        if self.correct > self.n:
            raise ValueError("r6_pass_commit_correct_exceeds_samples")
        if abs(self.accuracy - self.correct / self.n) > 1e-9:
            raise ValueError("r6_pass_commit_accuracy_disagrees_with_correct_count")
        if not math.isfinite(self.scalar_cer) or not math.isfinite(self.accuracy):
            raise ValueError("r6_pass_commit_metric_not_finite")
        return self


class BudgetRecord(BaseModel):
    """Cumulative wall/CPU charged to this experiment at publication time."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    wall_seconds: float = Field(ge=0)
    cpu_seconds: float = Field(ge=0)


class TrainingState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["english-r6-training-state-v2"]
    status: Literal["pass_complete"]
    candidate: str = Field(min_length=1)
    lineage_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    delta_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pass_index: int = Field(ge=1, le=3)
    data_epoch: int = Field(ge=1)
    optimizer_step: int = Field(ge=0)
    trainable_inventory: dict[str, ParameterRecord]
    tensor_count: int = Field(ge=1)
    tensors_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Committed passes live in the state, so a crash between committing and writing the
    # derived results view cannot lose or silently invent a completed pass.
    passes: list[PassCommit]
    budget: BudgetRecord
    torch_rng: list[int]
    python_rng_version: int
    python_rng_state: list[int]

    @model_validator(mode="after")
    def _contiguous_passes(self) -> "TrainingState":
        indices = [item.pass_index for item in self.passes]
        if indices != list(range(1, self.pass_index + 1)):
            raise ValueError("r6_state_committed_passes_are_not_contiguous")
        if not self.passes:
            raise ValueError("r6_state_requires_at_least_one_committed_pass")
        return self


class SelectedState(BaseModel):
    """Model-only winner snapshot; optimizer moments stay only in ``latest``."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["english-r6-selected-state-v2"]
    status: Literal["pass_complete"]
    weights_only: Literal[True]
    candidate: str = Field(min_length=1)
    lineage_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_inventory_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    delta_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pass_index: int = Field(ge=1, le=3)
    data_epoch: int = Field(ge=1)
    source_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trainable_inventory: dict[str, ParameterRecord]
    tensor_count: int = Field(ge=1)
    tensors_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StateLineage:
    lineage_hash: str
    recipe_hash: str
    base_inventory_sha256: str
    delta_sha256: str
    candidate: str


def _require_directory(path: Path, *, files: tuple[str, ...] | None = None) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("r6_state_directory_missing_or_unsafe")
    if files is not None:
        items = list(path.iterdir())
        if {item.name for item in items} != set(files):
            raise ValueError("r6_state_directory_file_inventory_changed")
        if any(item.is_symlink() or not item.is_file() for item in items):
            raise ValueError("r6_state_directory_requires_regular_files")


def _inventory_of(model_named: Iterable[tuple[str, Any]]) -> dict[str, ParameterRecord]:
    """Bare parameter names, matching the frozen R5 inventory convention.

    Tensor keys in the state file carry the ``model.`` prefix; the inventory does not, so
    Part A's delta/freeze code can compare it directly with ``inventory(named_trainable``
    ``(model))`` exactly as it does for the R5 selected state.
    """
    records: dict[str, ParameterRecord] = {}
    for name, parameter in model_named:
        records[name] = ParameterRecord(
            dtype=str(parameter.dtype).removeprefix("torch."),
            shape=[int(size) for size in parameter.shape],
        )
    return records


def _optimizer_tensors(model_named: Sequence[tuple[str, Any]], optimizer) -> dict[str, Any]:
    import torch

    tensors: dict[str, Any] = {}
    for name, parameter in model_named:
        entry = optimizer.state.get(parameter)
        if not entry or any(key not in entry for key in OPTIMIZER_KEYS):
            raise ValueError("r6_optimizer_state_missing_before_checkpoint")
        for key in OPTIMIZER_KEYS:
            value = entry[key]
            if not torch.is_tensor(value):
                raise ValueError("r6_optimizer_state_value_is_not_a_tensor")
            value = value.detach().to("cpu").contiguous().clone()
            if key == "step":
                if value.numel() != 1:
                    raise ValueError("r6_optimizer_step_is_not_scalar")
                value = value.reshape(1)
            elif [int(size) for size in value.shape] != [int(size) for size in parameter.shape]:
                raise ValueError("r6_optimizer_moment_shape_differs_from_parameter")
            tensors[f"optimizer.{name}.{key}"] = value
    return tensors


def parameter_bytes(inventory: Mapping[str, ParameterRecord | Mapping[str, Any]]) -> int:
    total = 0
    for record in inventory.values():
        shape = record["shape"] if isinstance(record, Mapping) else record.shape
        total += math.prod(int(size) for size in shape) * 4
    return total


def estimate_full_state_bytes(
    inventory: Mapping[str, Any], *, header_margin: int = 1_000_000
) -> int:
    """One resumable state: trainable weights, two AdamW moments, scalar steps, header."""
    weights = parameter_bytes(inventory)
    return 3 * weights + 8 * len(inventory) + header_margin


def estimate_selected_state_bytes(
    inventory: Mapping[str, Any], *, header_margin: int = 200_000
) -> int:
    return parameter_bytes(inventory) + 4 * len(inventory) + header_margin


def estimated_publication_peak(inventory: Mapping[str, Any]) -> int:
    return publication_peak_bytes(
        full_state_bytes=estimate_full_state_bytes(inventory),
        selected_state_bytes=estimate_selected_state_bytes(inventory),
    )


def save_state(
    directory: Path,
    *,
    model_named: list[tuple[str, Any]],
    optimizer,
    torch,
    python_random,
    lineage: StateLineage,
    pass_index: int,
    data_epoch: int,
    optimizer_step: int,
    passes: Sequence[PassCommit | Mapping[str, Any]],
    consumption: Consumption,
) -> TrainingState:
    """Write a new resumable state; the destination must not exist."""
    from safetensors.torch import save_file

    if directory.exists() or directory.is_symlink():
        raise ValueError("r6_state_destination_already_exists")
    inventory = _inventory_of(model_named)
    if not inventory:
        raise ValueError("r6_state_requires_trainable_parameters")
    tensors = {
        f"model.{name}": parameter.detach().to("cpu").contiguous().clone()
        for name, parameter in model_named
    }
    tensors.update(_optimizer_tensors(model_named, optimizer))
    torch_rng = torch.get_rng_state().tolist()
    python_version, python_state, gauss = python_random.getstate()
    if gauss is not None:
        raise ValueError("r6_python_rng_has_outstanding_gaussian_state")
    directory.mkdir(mode=0o700, parents=False)
    weights = directory / "state.safetensors"
    save_file(tensors, str(weights))
    state = TrainingState(
        version="english-r6-training-state-v2",
        status="pass_complete",
        candidate=lineage.candidate,
        lineage_hash=lineage.lineage_hash,
        recipe_hash=lineage.recipe_hash,
        base_inventory_sha256=lineage.base_inventory_sha256,
        delta_sha256=lineage.delta_sha256,
        pass_index=pass_index,
        data_epoch=data_epoch,
        optimizer_step=optimizer_step,
        trainable_inventory=inventory,
        tensor_count=len(tensors),
        tensors_sha256=digest_file(weights),
        passes=[
            item if isinstance(item, PassCommit) else PassCommit.model_validate(item)
            for item in passes
        ],
        budget=BudgetRecord(
            wall_seconds=float(consumption.wall_seconds),
            cpu_seconds=float(consumption.cpu_seconds),
        ),
        torch_rng=torch_rng,
        python_rng_version=int(python_version),
        python_rng_state=[int(value) for value in python_state],
    )
    (directory / "state.json").write_text(
        json.dumps(state.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return state


def load_state(directory: Path) -> tuple[dict[str, Any], TrainingState]:
    from safetensors.torch import load_file

    state = TrainingState.model_validate_json(
        (directory / "state.json").read_text(encoding="utf-8")
    )
    if state.version != STATE_VERSION or state.status != "pass_complete":
        raise ValueError("r6_state_version_or_status_mismatch")
    weights = directory / "state.safetensors"
    if digest_file(weights) != state.tensors_sha256:
        raise ValueError("r6_state_weights_hash_mismatch")
    tensors = load_file(str(weights), device="cpu")
    expected = {f"model.{name}" for name in state.trainable_inventory}
    expected |= {
        f"optimizer.{name}.{key}" for name in state.trainable_inventory for key in OPTIMIZER_KEYS
    }
    if set(tensors) != expected or state.tensor_count != len(tensors):
        raise ValueError("r6_state_tensor_inventory_changed")
    for name, record in state.trainable_inventory.items():
        value = tensors[f"model.{name}"]
        if [int(size) for size in value.shape] != record.shape:
            raise ValueError("r6_state_tensor_shape_changed")
        if str(value.dtype).removeprefix("torch.") != record.dtype:
            raise ValueError("r6_state_tensor_dtype_changed")
        for key in ("exp_avg", "exp_avg_sq"):
            moment = tensors[f"optimizer.{name}.{key}"]
            if [int(size) for size in moment.shape] != record.shape:
                raise ValueError("r6_state_optimizer_shape_changed")
        if tensors[f"optimizer.{name}.step"].numel() != 1:
            raise ValueError("r6_state_optimizer_step_shape_changed")
    return tensors, state


def restore_state(model, optimizer, tensors: dict[str, Any], state: TrainingState) -> None:
    """Copy weights and AdamW moments back, verifying names and shapes again."""
    import torch

    named = [
        (name, parameter) for name, parameter in model.named_parameters() if parameter.requires_grad
    ]
    expected = state.trainable_inventory
    observed = _inventory_of(named)
    if observed != expected:
        raise ValueError("r6_resume_trainable_parameter_names_changed")
    for name, parameter in named:
        record = expected[name]
        value = tensors[f"model.{name}"]
        if [int(size) for size in parameter.shape] != record.shape:
            raise ValueError("r6_resume_parameter_shape_changed")
        with torch.no_grad():
            parameter.copy_(value)
        entry = optimizer.state[parameter]
        entry["exp_avg"] = tensors[f"optimizer.{name}.exp_avg"].clone()
        entry["exp_avg_sq"] = tensors[f"optimizer.{name}.exp_avg_sq"].clone()
        step = tensors[f"optimizer.{name}.step"]
        entry["step"] = step.reshape(()).clone() if step.numel() == 1 else step.clone()


def restore_rng(torch, python_random, state: TrainingState) -> None:
    if not state.torch_rng or not state.python_rng_state:
        raise ValueError("r6_state_rng_payload_is_empty")
    if min(state.torch_rng) < 0 or max(state.torch_rng) > 255:
        raise ValueError("r6_state_torch_rng_is_not_byte_encoded")
    torch.set_rng_state(torch.tensor(state.torch_rng, dtype=torch.uint8).cpu())
    python_random.setstate((state.python_rng_version, tuple(state.python_rng_state), None))


def verify_resume(
    state: TrainingState,
    *,
    lineage: StateLineage,
    trainable_inventory: Mapping[str, ParameterRecord],
    pass_limit: int,
    data_epochs: Sequence[int],
) -> Consumption:
    """Reject a resume under changed code, inputs, recipe, initialization or scorer."""
    if (
        state.candidate != lineage.candidate
        or state.lineage_hash != lineage.lineage_hash
        or state.recipe_hash != lineage.recipe_hash
        or state.base_inventory_sha256 != lineage.base_inventory_sha256
        or state.delta_sha256 != lineage.delta_sha256
    ):
        raise ValueError("r6_resume_lineage_changed")
    observed = {
        name: record
        if isinstance(record, ParameterRecord)
        else ParameterRecord.model_validate(record)
        for name, record in trainable_inventory.items()
    }
    if state.trainable_inventory != observed:
        raise ValueError("r6_resume_trainable_inventory_changed")
    if [item.data_epoch for item in state.passes] != list(data_epochs):
        raise ValueError("r6_resume_data_epoch_sequence_changed")
    if state.pass_index >= pass_limit:
        raise ValueError("r6_resume_has_no_remaining_bounded_pass")
    return Consumption(wall_seconds=state.budget.wall_seconds, cpu_seconds=state.budget.cpu_seconds)


def save_selected(
    directory: Path,
    *,
    model_named: list[tuple[str, Any]],
    lineage: StateLineage,
    pass_index: int,
    data_epoch: int,
    source_state_sha256: str,
) -> SelectedState:
    from safetensors.torch import save_file

    if directory.exists() or directory.is_symlink():
        raise ValueError("r6_selected_state_destination_already_exists")
    inventory = _inventory_of(model_named)
    if not inventory:
        raise ValueError("r6_selected_state_requires_trainable_parameters")
    tensors = {
        f"model.{name}": parameter.detach().to("cpu").contiguous().clone()
        for name, parameter in model_named
    }
    directory.mkdir(mode=0o700, parents=False)
    weights = directory / "state.safetensors"
    save_file(tensors, str(weights))
    state = SelectedState(
        version="english-r6-selected-state-v2",
        status="pass_complete",
        weights_only=True,
        candidate=lineage.candidate,
        lineage_hash=lineage.lineage_hash,
        recipe_hash=lineage.recipe_hash,
        base_inventory_sha256=lineage.base_inventory_sha256,
        delta_sha256=lineage.delta_sha256,
        pass_index=pass_index,
        data_epoch=data_epoch,
        source_state_sha256=source_state_sha256,
        trainable_inventory=inventory,
        tensor_count=len(tensors),
        tensors_sha256=digest_file(weights),
    )
    (directory / "state.json").write_text(
        json.dumps(state.model_dump(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return state


def load_selected(directory: Path) -> tuple[dict[str, Any], SelectedState]:
    from safetensors.torch import load_file

    state = SelectedState.model_validate_json(
        (directory / "state.json").read_text(encoding="utf-8")
    )
    if (
        state.version != SELECTED_VERSION
        or state.status != "pass_complete"
        or state.weights_only is not True
    ):
        raise ValueError("r6_selected_state_version_or_status_mismatch")
    weights = directory / "state.safetensors"
    if digest_file(weights) != state.tensors_sha256:
        raise ValueError("r6_selected_state_weights_hash_mismatch")
    tensors = load_file(str(weights), device="cpu")
    expected = {f"model.{name}" for name in state.trainable_inventory}
    if set(tensors) != expected or state.tensor_count != len(tensors):
        raise ValueError("r6_selected_state_tensor_inventory_changed")
    for name, record in state.trainable_inventory.items():
        value = tensors[f"model.{name}"]
        if [int(size) for size in value.shape] != record.shape:
            raise ValueError("r6_selected_state_tensor_shape_changed")
        if str(value.dtype).removeprefix("torch.") != record.dtype:
            raise ValueError("r6_selected_state_tensor_dtype_changed")
    return tensors, state


def _reclaim(output: Path, name: str) -> None:
    target = output / name
    if target.is_symlink():
        raise ValueError("r6_transient_state_must_not_be_a_symlink")
    if target.exists():
        if not target.is_dir():
            raise ValueError("r6_transient_state_is_not_a_directory")
        shutil.rmtree(target)


ROTATION_LEFTOVERS = ("latest.new", "latest.old", "best.old")


def clean_rotation_leftovers(output: Path) -> list[str]:
    """Reclaim crash leftovers before writing, so the publication peak stays bounded.

    ``selected-candidate`` is deliberately kept: it is the only copy of a committed
    winner when a crash landed between state publication and winner promotion.
    """
    removed: list[str] = []
    for name in ROTATION_LEFTOVERS:
        target = output / name
        if target.exists() or target.is_symlink():
            _reclaim(output, name)
            removed.append(name)
    return removed


def discard_selected_candidate(output: Path) -> bool:
    target = output / "selected-candidate"
    if not target.exists() and not target.is_symlink():
        return False
    _reclaim(output, "selected-candidate")
    return True


def publish_latest(output: Path, **kwargs: Any) -> TrainingState:
    """Write and rotate ``latest`` without ever removing the only recoverable state."""
    staged = output / "latest.new"
    _reclaim(output, "latest.new")
    state = save_state(staged, **kwargs)
    latest = output / "latest"
    previous = output / "latest.old"
    _reclaim(output, "latest.old")
    if latest.exists() or latest.is_symlink():
        if latest.is_symlink() or not latest.is_dir():
            raise ValueError("r6_latest_state_is_not_a_directory")
        latest.rename(previous)
    staged.rename(latest)
    _reclaim(output, "latest.old")
    return state


def promote_selected(output: Path) -> None:
    """Rotate ``best`` from the prepared candidate without destroying the previous winner."""
    candidate = output / "selected-candidate"
    if not candidate.is_dir() or candidate.is_symlink():
        raise ValueError("r6_selected_candidate_missing")
    best = output / "best"
    previous = output / "best.old"
    _reclaim(output, "best.old")
    if best.exists() or best.is_symlink():
        if best.is_symlink() or not best.is_dir():
            raise ValueError("r6_selected_checkpoint_target_not_a_directory")
        best.rename(previous)
    candidate.rename(best)
    _reclaim(output, "best.old")


def write_selected_candidate(output: Path, **kwargs: Any) -> SelectedState:
    candidate = output / "selected-candidate"
    _reclaim(output, "selected-candidate")
    return save_selected(candidate, **kwargs)


def retire_latest(output: Path) -> bool:
    """Drop the resumable state once no bounded pass remains.

    ``verify_resume`` already refuses a state whose ``pass_index`` reached the pass limit,
    so this removes only duplicated optimizer state; the model-only winner remains. It is
    what keeps the finished run inside the output ceiling.
    """
    latest = output / "latest"
    if latest.is_symlink() or not latest.is_dir():
        return False
    shutil.rmtree(latest)
    return True
