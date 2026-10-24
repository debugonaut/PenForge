"""Explicit R5 trainable-parameter scope, mirroring the audited R4 scope."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

TRAINABLE_SCOPE = "last_two_decoder_layers_embedding_output_projection"


def select_trainable(model: Any) -> list[Any]:
    """Freeze everything, then unfreeze the bounded decoder subset.

    The encoder stays frozen and in eval mode: R5 changes the training schedule and
    augmentation, not the model architecture or the encoder.
    """
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    decoder = model.decoder.model.decoder
    layers = list(decoder.layers)
    if len(layers) < 2:
        raise ValueError("r5_decoder_has_fewer_than_two_layers")
    modules = [*layers[-2:], decoder.embed_tokens, model.decoder.output_projection]
    for module in modules:
        for parameter in module.parameters():
            parameter.requires_grad_(True)
    selected = [parameter for parameter in model.parameters() if parameter.requires_grad]
    if not selected:
        raise ValueError("r5_trocr_trainable_parameter_scope_is_empty")
    if any(parameter.requires_grad for parameter in model.encoder.parameters()):
        raise ValueError("r5_encoder_must_stay_frozen")
    return selected


def named_trainable(model: Any) -> list[tuple[str, Any]]:
    return [
        (name, parameter) for name, parameter in model.named_parameters() if parameter.requires_grad
    ]


def inventory(named: Iterable[tuple[str, Any]]) -> dict[str, dict[str, Any]]:
    """Stable name -> dtype/shape map used to verify checkpoints and resumes."""
    result: dict[str, dict[str, Any]] = {}
    for name, parameter in named:
        result[name] = {
            "dtype": str(parameter.dtype).removeprefix("torch."),
            "shape": [int(size) for size in parameter.shape],
        }
    if not result:
        raise ValueError("r5_parameter_inventory_is_empty")
    return result


def parameter_count(named: Iterable[tuple[str, Any]]) -> int:
    import math

    return sum(math.prod(int(size) for size in parameter.shape) for _, parameter in named)


def set_eval_mode(model: Any) -> None:
    """Frozen encoder in eval; trainable decoder modules in train mode."""
    model.train()
    model.encoder.eval()
