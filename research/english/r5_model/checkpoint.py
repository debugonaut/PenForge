"""Self-contained R5 training state: trainable weights, optimizer, RNG and progress.

R4 wrote model-only checkpoints, so a run could not resume and its optimizer/RNG state
was unrecoverable. R5 stores only the trainable decoder subset (the encoder is frozen),
its AdamW moments, both random-number states and an explicit lineage record in a
two-file directory. Every load re-verifies the inventory, hashes and lineage.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from research.pretrained.artifacts import digest_file

STATE_VERSION = "english-r5-training-state-v1"
SELECTED_VERSION = "english-r5-selected-state-v1"
STATE_FILES = ("state.safetensors", "state.json")
OPTIMIZER_KEYS = ("exp_avg", "exp_avg_sq", "step")


class ParameterRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dtype: str
    shape: list[int]


class SelectedState(BaseModel):
    """Model-only winner snapshot; optimizer moments stay only in ``latest``.

    Keeping a full optimizer state for both ``latest`` and ``best`` exceeded the run's
    output ceiling once Part A measured the 336 MB trainable subset. The winner is never
    resumed from, so it stores weights only and points at the full state it came from.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["english-r5-selected-state-v1"]
    # Part A's freeze reads the same field name as the resumable state. The distinct
    # version and the explicit weights_only flag keep the two formats unambiguous.
    status: Literal["pass_complete"]
    weights_only: Literal[True]
    lineage_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    init_checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pass_index: int = Field(ge=1, le=4)
    source_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trainable_inventory: dict[str, ParameterRecord]
    tensor_count: int = Field(ge=1)
    tensors_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrainingState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["english-r5-training-state-v1"]
    status: Literal["pass_complete"]
    lineage_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipe_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    bundle_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    init_checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pass_index: int = Field(ge=1, le=4)
    optimizer_step: int = Field(ge=0)
    trainable_inventory: dict[str, ParameterRecord]
    tensor_count: int = Field(ge=1)
    tensors_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    torch_rng: list[int]
    python_rng_version: int
    python_rng_state: list[int]


@dataclass(frozen=True)
class StateLineage:
    lineage_hash: str
    recipe_hash: str
    bundle_hash: str
    init_checkpoint_sha256: str


def _require_directory(path: Path, *, files: tuple[str, ...] | None = None) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("r5_state_directory_missing_or_unsafe")
    if files is not None:
        names = {item.name for item in path.iterdir()}
        if names != set(files):
            raise ValueError("r5_state_directory_file_inventory_changed")
        if any(item.is_symlink() or not item.is_file() for item in path.iterdir()):
            raise ValueError("r5_state_directory_requires_regular_files")


def tensor_inventory(tensors: dict[str, Any]) -> dict[str, ParameterRecord]:
    return {
        name: ParameterRecord(
            dtype=str(value.dtype).removeprefix("torch."),
            shape=[int(size) for size in value.shape],
        )
        for name, value in tensors.items()
    }


def _optimizer_tensors(model_named: Iterable[tuple[str, Any]], optimizer) -> dict[str, Any]:
    import torch

    tensors: dict[str, Any] = {}
    for name, parameter in model_named:
        entry = optimizer.state.get(parameter)
        if not entry or any(key not in entry for key in OPTIMIZER_KEYS):
            raise ValueError("r5_optimizer_state_missing_before_checkpoint")
        for key in OPTIMIZER_KEYS:
            value = entry[key]
            if not torch.is_tensor(value):
                raise ValueError("r5_optimizer_state_value_is_not_a_tensor")
            value = value.detach().to("cpu").contiguous().clone()
            if key == "step":
                if value.numel() != 1:
                    raise ValueError("r5_optimizer_step_is_not_scalar")
                value = value.reshape(1)
            elif [int(size) for size in value.shape] != [int(size) for size in parameter.shape]:
                raise ValueError("r5_optimizer_moment_shape_differs_from_parameter")
            tensors[f"optimizer.{name}.{key}"] = value
    return tensors


def save_state(
    directory: Path,
    *,
    model_named: list[tuple[str, Any]],
    optimizer,
    torch,
    python_random,
    lineage: StateLineage,
    pass_index: int,
    optimizer_step: int,
) -> TrainingState:
    """Write a new state directory; callers must pass a path that does not exist."""
    from safetensors.torch import save_file

    if directory.exists() or directory.is_symlink():
        raise ValueError("r5_state_destination_already_exists")
    inventory = {
        name: ParameterRecord(
            dtype=str(parameter.dtype).removeprefix("torch."),
            shape=[int(size) for size in parameter.shape],
        )
        for name, parameter in model_named
    }
    if not inventory:
        raise ValueError("r5_state_requires_trainable_parameters")
    tensors = {
        f"model.{name}": parameter.detach().to("cpu").contiguous().clone()
        for name, parameter in model_named
    }
    tensors.update(_optimizer_tensors(model_named, optimizer))
    torch_rng = torch.get_rng_state().tolist()
    python_version, python_state, gauss = python_random.getstate()
    if gauss is not None:
        raise ValueError("r5_python_rng_has_outstanding_gaussian_state")
    directory.mkdir(mode=0o700, parents=False)
    weights = directory / "state.safetensors"
    save_file(tensors, str(weights))
    state = TrainingState(
        version="english-r5-training-state-v1",
        status="pass_complete",
        lineage_hash=lineage.lineage_hash,
        recipe_hash=lineage.recipe_hash,
        bundle_hash=lineage.bundle_hash,
        init_checkpoint_sha256=lineage.init_checkpoint_sha256,
        pass_index=pass_index,
        optimizer_step=optimizer_step,
        trainable_inventory=inventory,
        tensor_count=len(tensors),
        tensors_sha256=digest_file(weights),
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

    _require_directory(directory, files=STATE_FILES)
    state_path = directory / "state.json"
    state = TrainingState.model_validate_json(state_path.read_text(encoding="utf-8"))
    if state.version != STATE_VERSION or state.status != "pass_complete":
        raise ValueError("r5_state_version_or_status_mismatch")
    weights = directory / "state.safetensors"
    if digest_file(weights) != state.tensors_sha256:
        raise ValueError("r5_state_weights_hash_mismatch")
    if weights.stat().st_size > 2_000_000_000:
        raise ValueError("r5_state_weights_exceed_bounded_size")
    tensors = load_file(str(weights), device="cpu")
    expected = {f"model.{name}" for name in state.trainable_inventory}
    expected |= {
        f"optimizer.{name}.{key}" for name in state.trainable_inventory for key in OPTIMIZER_KEYS
    }
    if set(tensors) != expected or state.tensor_count != len(tensors):
        raise ValueError("r5_state_tensor_inventory_changed")
    for name, record in state.trainable_inventory.items():
        value = tensors[f"model.{name}"]
        if [int(size) for size in value.shape] != record.shape:
            raise ValueError("r5_state_tensor_shape_changed")
        if str(value.dtype).removeprefix("torch.") != record.dtype:
            raise ValueError("r5_state_tensor_dtype_changed")
        for key in ("exp_avg", "exp_avg_sq"):
            moment = tensors[f"optimizer.{name}.{key}"]
            if [int(size) for size in moment.shape] != record.shape:
                raise ValueError("r5_state_optimizer_shape_changed")
        if tensors[f"optimizer.{name}.step"].numel() != 1:
            raise ValueError("r5_state_optimizer_step_shape_changed")
    return tensors, state


def restore_state(model, optimizer, tensors: dict[str, Any], state: TrainingState) -> None:
    """Copy weights and optimizer moments back, verifying names and shapes again."""
    import torch

    named = list(model.named_parameters())
    expected = state.trainable_inventory
    if {name for name, _ in named} != set(expected):
        # Compare only the trainable subset; frozen parameters have no record.
        trainable = {name for name, parameter in named if parameter.requires_grad}
        if trainable != set(expected):
            raise ValueError("r5_resume_trainable_parameter_names_changed")
        named = [(name, parameter) for name, parameter in named if name in expected]
    for name, parameter in named:
        record = expected[name]
        value = tensors[f"model.{name}"]
        if [int(size) for size in parameter.shape] != record.shape:
            raise ValueError("r5_resume_parameter_shape_changed")
        with torch.no_grad():
            parameter.copy_(value)
        entry = optimizer.state[parameter]
        entry["exp_avg"] = tensors[f"optimizer.{name}.exp_avg"].clone()
        entry["exp_avg_sq"] = tensors[f"optimizer.{name}.exp_avg_sq"].clone()
        step = tensors[f"optimizer.{name}.step"]
        entry["step"] = step.reshape(()).clone() if step.numel() == 1 else step.clone()


def restore_rng(torch, python_random, state: TrainingState) -> None:
    if not state.torch_rng or not state.python_rng_state:
        raise ValueError("r5_state_rng_payload_is_empty")
    if min(state.torch_rng) < 0 or max(state.torch_rng) > 255:
        raise ValueError("r5_state_torch_rng_is_not_byte_encoded")
    torch.set_rng_state(torch.tensor(state.torch_rng, dtype=torch.uint8).cpu())
    python_random.setstate((state.python_rng_version, tuple(state.python_rng_state), None))


def _plain(record: ParameterRecord | Mapping[str, Any]) -> dict[str, Any]:
    return record.model_dump() if isinstance(record, ParameterRecord) else dict(record)


def verify_resume(
    state: TrainingState,
    *,
    lineage: StateLineage,
    trainable_inventory: Mapping[str, ParameterRecord | Mapping[str, Any]],
    pass_limit: int,
) -> None:
    if (
        state.lineage_hash != lineage.lineage_hash
        or state.recipe_hash != lineage.recipe_hash
        or state.bundle_hash != lineage.bundle_hash
        or state.init_checkpoint_sha256 != lineage.init_checkpoint_sha256
    ):
        raise ValueError("r5_resume_lineage_changed")
    expected = {name: _plain(record) for name, record in state.trainable_inventory.items()}
    observed = {name: _plain(record) for name, record in trainable_inventory.items()}
    if expected != observed:
        raise ValueError("r5_resume_trainable_inventory_changed")
    if state.pass_index >= pass_limit:
        raise ValueError("r5_resume_has_no_remaining_bounded_pass")


def save_selected(
    directory: Path,
    *,
    model_named: list[tuple[str, Any]],
    lineage: StateLineage,
    pass_index: int,
    source_state_sha256: str,
) -> SelectedState:
    """Write a model-only winner snapshot next to the full resumable state."""
    from safetensors.torch import save_file

    if directory.exists() or directory.is_symlink():
        raise ValueError("r5_selected_state_destination_already_exists")
    inventory = {
        name: ParameterRecord(
            dtype=str(parameter.dtype).removeprefix("torch."),
            shape=[int(size) for size in parameter.shape],
        )
        for name, parameter in model_named
    }
    if not inventory:
        raise ValueError("r5_selected_state_requires_trainable_parameters")
    tensors = {
        f"model.{name}": parameter.detach().to("cpu").contiguous().clone()
        for name, parameter in model_named
    }
    directory.mkdir(mode=0o700, parents=False)
    weights = directory / "state.safetensors"
    save_file(tensors, str(weights))
    state = SelectedState(
        version="english-r5-selected-state-v1",
        status="pass_complete",
        weights_only=True,
        lineage_hash=lineage.lineage_hash,
        recipe_hash=lineage.recipe_hash,
        bundle_hash=lineage.bundle_hash,
        init_checkpoint_sha256=lineage.init_checkpoint_sha256,
        pass_index=pass_index,
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
    """Load and verify the model-only winner snapshot."""
    from safetensors.torch import load_file

    _require_directory(directory, files=STATE_FILES)
    state = SelectedState.model_validate_json(
        (directory / "state.json").read_text(encoding="utf-8")
    )
    if (
        state.version != SELECTED_VERSION
        or state.status != "pass_complete"
        or state.weights_only is not True
    ):
        raise ValueError("r5_selected_state_version_or_status_mismatch")
    weights = directory / "state.safetensors"
    if digest_file(weights) != state.tensors_sha256:
        raise ValueError("r5_selected_state_weights_hash_mismatch")
    if weights.stat().st_size > 600_000_000:
        raise ValueError("r5_selected_state_weights_exceed_bounded_size")
    tensors = load_file(str(weights), device="cpu")
    expected = {f"model.{name}" for name in state.trainable_inventory}
    if set(tensors) != expected or state.tensor_count != len(tensors):
        raise ValueError("r5_selected_state_tensor_inventory_changed")
    for name, record in state.trainable_inventory.items():
        value = tensors[f"model.{name}"]
        if [int(size) for size in value.shape] != record.shape:
            raise ValueError("r5_selected_state_tensor_shape_changed")
        if str(value.dtype).removeprefix("torch.") != record.dtype:
            raise ValueError("r5_selected_state_tensor_dtype_changed")
    return tensors, state
