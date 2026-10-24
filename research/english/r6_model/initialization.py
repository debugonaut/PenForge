"""Strict R5-winner reconstruction and R6 initialization lineage.

The frozen R5 winner is a *trainable-parameter delta*, not a standalone model. R6 must
start from the exact R4 full checkpoint that the R5 run used, then copy the selected 53
``model.*`` tensors onto it. Nothing here ever falls back to the pretrained bundle or to
R4 weights without the delta.

Metadata verification (:func:`verify_initialization`) is data-only: it parses configs,
JSON state files and safetensors headers (never tensor payloads through a framework).
:func:`apply_delta` is the owner-run step that copies verified bytes onto a loaded model,
and rejects missing keys, extra keys, shape/dtype mismatches and a non-trainable target.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.english.r6_contract import R5Initialization, tensor_header
from research.pretrained.artifacts import canonical_hash, digest_file

POLICY = "verified_r4_base_plus_r5_selected_trainable_delta"
# The frozen R5 composition's effective token ids (root of the canonicalised checkpoint).
EXPECTED_TOKEN_IDS: dict[str, int] = {
    "decoder_start_token_id": 2,
    "eos_token_id": 2,
    "pad_token_id": 1,
}
TOKEN_KEYS = tuple(EXPECTED_TOKEN_IDS)
BASE_FILES = frozenset({"config.json", "generation_config.json", "model.safetensors"})
DELTA_FILES = frozenset({"state.json", "state.safetensors"})
EMBED_KEY_SUFFIX = "model.decoder.embed_tokens.weight"
PROJECTION_PREFIX = "decoder.output_projection."


@dataclass(frozen=True)
class ExpectedHashes:
    """Injectable frozen-winner hashes.

    ``None`` means Part A's pinned contract constants, which is the only correct setting
    for a real run. The fields exist so inert fixtures and a future explicit contract can
    be verified without weakening the production check.
    """

    winner_sha256: str | None = None
    delta_sha256: str | None = None


@dataclass(frozen=True)
class Initialization:
    """The three pinned inputs of the R5 composition, all absolute directories."""

    base_checkpoint: Path
    delta_checkpoint: Path
    winner_file: Path

    def resolved(self) -> "Initialization":
        return Initialization(
            base_checkpoint=Path(self.base_checkpoint).resolve(),
            delta_checkpoint=Path(self.delta_checkpoint).resolve(),
            winner_file=Path(self.winner_file).resolve(),
        )


def _require_flat_directory(path: Path, expected: frozenset[str], label: str) -> dict[str, str]:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"r6_{label}_directory_missing_or_unsafe")
    items = sorted(path.iterdir())
    if {item.name for item in items} != set(expected):
        raise ValueError(f"r6_{label}_file_inventory_changed")
    if any(item.is_symlink() or not item.is_file() for item in items):
        raise ValueError(f"r6_{label}_requires_regular_files")
    return {item.name: digest_file(item) for item in items}


def _token_map(config: Mapping[str, Any]) -> dict[str, Any]:
    """Read token ids from the checkpoint root or the nested decoder config."""
    decoder = config.get("decoder", {})
    return {key: config[key] if key in config else decoder.get(key) for key in TOKEN_KEYS}


def read_config(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("r6_configuration_file_missing_or_unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("r6_configuration_file_not_an_object")
    return value


def check_architecture(config: Mapping[str, Any], *, label: str) -> int:
    """Confirm the pinned TrOCR composition and return its decoder layer count."""
    if (
        config.get("model_type") != "vision-encoder-decoder"
        or config.get("encoder", {}).get("model_type") != "vit"
        or config.get("decoder", {}).get("model_type") != "trocr"
    ):
        raise ValueError(f"r6_{label}_architecture_changed")
    layers = config.get("decoder", {}).get("decoder_layers")
    if type(layers) is not int or not 2 <= layers <= 64:
        raise ValueError(f"r6_{label}_decoder_layer_count_unavailable")
    tokens = _token_map(config)
    if tokens != EXPECTED_TOKEN_IDS:
        raise ValueError(f"r6_{label}_token_ids_changed_from_frozen_r5_composition")
    return layers


def expected_trainable_keys(base_header: Mapping[str, Any], decoder_layers: int) -> set[str]:
    """The exact R5 trainable scope, derived from the actual base checkpoint.

    R5 unfroze the last two decoder layers, the token embedding and the output
    projection. This checkpoint ties ``decoder.output_projection.weight`` to the token
    embedding, so the projection is stored once, as ``decoder.model.decoder.embed_tokens
    .weight``; if a base ever stored a separate projection its keys are required too.
    """
    if decoder_layers < 2:
        raise ValueError("r6_base_decoder_has_fewer_than_two_layers")
    last_two = {decoder_layers - 2, decoder_layers - 1}
    keys = {name for name in base_header if name.endswith(EMBED_KEY_SUFFIX)}
    keys |= {name for name in base_header if name.startswith(PROJECTION_PREFIX)}
    for index in last_two:
        keys |= {name for name in base_header if f".layers.{index}." in name}
    if not keys:
        raise ValueError("r6_expected_trainable_scope_is_empty")
    return keys


def verify_initialization(
    initialization: Initialization,
    *,
    expected: ExpectedHashes | None = None,
) -> dict[str, Any]:
    """Data-only verification of the complete frozen R5 composition.

    Part A's :class:`R5Initialization` owns the frozen-winner contract; R6 delegates to it
    and then proves that the delta is *exactly* the trainable scope of this base, with
    matching shapes, so a partial or over-broad delta cannot initialise a run.
    """
    resolved = initialization.resolved()
    contract = R5Initialization(
        base_checkpoint=resolved.base_checkpoint,
        delta_checkpoint=resolved.delta_checkpoint,
        winner_file=resolved.winner_file,
    )
    expectations = ExpectedHashes() if expected is None else expected
    kwargs: dict[str, str] = {}
    if expectations.winner_sha256 is not None:
        kwargs["expected_winner"] = expectations.winner_sha256
    if expectations.delta_sha256 is not None:
        kwargs["expected_delta"] = expectations.delta_sha256
    metadata = dict(contract.verify(**kwargs))

    base_files = _require_flat_directory(resolved.base_checkpoint, BASE_FILES, "base_checkpoint")
    if base_files != metadata["base_files"]:
        raise ValueError("r6_base_checkpoint_files_differ_from_frozen_winner")
    delta_files = _require_flat_directory(
        resolved.delta_checkpoint, DELTA_FILES, "delta_checkpoint"
    )
    if delta_files != metadata["delta_files"]:
        raise ValueError("r6_delta_checkpoint_files_differ_from_frozen_winner")

    base_config = read_config(resolved.base_checkpoint / "config.json")
    layers = check_architecture(base_config, label="base")
    base_header = tensor_header(resolved.base_checkpoint / "model.safetensors")
    delta_header = tensor_header(resolved.delta_checkpoint / "state.safetensors")

    expected_keys = expected_trainable_keys(base_header, layers)
    observed = {name.removeprefix("model.") for name in delta_header}
    if not all(name.startswith("model.") for name in delta_header):
        raise ValueError("r6_delta_contains_non_model_tensors")
    missing = expected_keys - observed
    extra = observed - expected_keys
    if missing or extra:
        raise ValueError("r6_delta_does_not_cover_exactly_the_r5_trainable_scope")
    for name in sorted(expected_keys):
        reference = base_header[name]
        delta = delta_header[f"model.{name}"]
        if delta["shape"] != reference["shape"]:
            raise ValueError("r6_delta_shape_differs_from_base_checkpoint")
        if delta["dtype"] != reference["dtype"]:
            raise ValueError("r6_delta_dtype_differs_from_base_checkpoint")

    inventory = metadata["trainable_inventory"]
    if set(inventory) != expected_keys:
        raise ValueError("r6_delta_inventory_differs_from_derived_trainable_scope")
    for name, record in inventory.items():
        if record["shape"] != delta_header[f"model.{name}"]["shape"]:
            raise ValueError("r6_delta_inventory_shape_differs_from_tensor_header")

    return {
        "policy": POLICY,
        "base_checkpoint": str(resolved.base_checkpoint),
        "base_files": base_files,
        "base_inventory_sha256": canonical_hash(base_files),
        "base_tensor_count": len(base_header),
        "delta_checkpoint": str(resolved.delta_checkpoint),
        "delta_files": delta_files,
        "delta_sha256": delta_files["state.safetensors"],
        "winner_sha256": metadata["winner_sha256"],
        "trainable_keys_sha256": canonical_hash(sorted(expected_keys)),
        "trainable_tensor_count": len(expected_keys),
        "trainable_inventory": {name: inventory[name] for name in sorted(inventory)},
        "tensor_header_sha256": metadata["tensor_header_sha256"],
        "decoder_layers": layers,
        "effective_token_ids": dict(EXPECTED_TOKEN_IDS),
        "optimizer": "new_no_r5_optimizer_in_selected_state",
        "delta_pass_index": 3,
    }


def verify_reconstruction_against_inventory(
    inventory: Mapping[str, Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any],
) -> None:
    """Require a real model's trainable inventory to equal the verified delta inventory."""
    if inventory != metadata["trainable_inventory"]:
        raise ValueError("r6_runtime_trainable_inventory_differs_from_verified_delta")
    if len(inventory) != metadata["trainable_tensor_count"]:
        raise ValueError("r6_runtime_trainable_tensor_count_changed")


def read_delta_tensors(path: Path) -> dict[str, Any]:
    """Owner-run only: deserialize the verified delta's tensor payload."""
    from safetensors.torch import load_file

    if path.is_symlink() or not path.is_file():
        raise ValueError("r6_delta_tensor_file_missing_or_unsafe")
    return load_file(str(path), device="cpu")


def apply_delta(
    torch,
    model,
    tensors: Mapping[str, Any],
    inventory: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Copy the verified delta onto the freshly initialised base model, strictly.

    Rejects a missing or extra delta key, a shape/dtype mismatch and any trainable
    parameter outside the delta scope. ``select_trainable`` must already have been called,
    so the target set is the R5 scope and the encoder must still be frozen.
    """
    from research.english.r5_model.parameters import named_trainable

    named = named_trainable(model)
    names = [name for name, _ in named]
    if len(set(names)) != len(names):
        raise ValueError("r6_runtime_trainable_parameter_names_are_not_unique")
    if set(names) != set(inventory):
        raise ValueError("r6_runtime_trainable_names_differ_from_delta_inventory")
    expected_tensors = {f"model.{name}" for name in names}
    missing = expected_tensors - set(tensors)
    extra = set(tensors) - expected_tensors
    if missing:
        raise ValueError("r6_delta_is_missing_required_trainable_tensors")
    if extra:
        raise ValueError("r6_delta_contains_unexpected_tensors")
    if any(parameter.requires_grad for parameter in model.encoder.parameters()):
        raise ValueError("r6_encoder_must_stay_frozen_during_delta_application")
    applied = 0
    with torch.no_grad():
        for name, parameter in named:
            value = tensors[f"model.{name}"]
            if list(value.shape) != [int(size) for size in parameter.shape]:
                raise ValueError("r6_delta_tensor_shape_differs_from_model")
            if value.dtype != parameter.dtype:
                raise ValueError("r6_delta_tensor_dtype_differs_from_model")
            parameter.copy_(value)
            applied += 1
    if applied != len(inventory):
        raise ValueError("r6_delta_application_incomplete")
    return {
        "applied_tensors": applied,
        "policy": POLICY,
    }
