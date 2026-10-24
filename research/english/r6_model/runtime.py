"""Owner-run runtime adapter: pinned base, verified delta, no silent fallback.

Importing this module imports no framework. :meth:`ContinuationRuntime.load_composition`
loads the exact R4 full checkpoint through the existing strict loader, applies the frozen
R5 trainable delta, and refuses to continue if the runtime trainable inventory differs
from the verified delta inventory. There is no path that returns a model without the
delta, and no path that returns a model when the base hash changed.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research.english.r6_model import initialization as initialization_module
from research.english.r6_model.recipe import R6ModelRecipe
from research.pretrained.artifacts import digest_file


class ContinuationRuntime:
    """Thin adapter over the pinned bundle, tokenizer, processor and strict loader."""

    def __init__(self, bundle: Path) -> None:
        self.bundle = Path(bundle).resolve()

    def setup(self, recipe: R6ModelRecipe):
        import random

        import torch

        random.seed(recipe.seed)
        torch.manual_seed(recipe.seed)
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        torch.use_deterministic_algorithms(True)
        return torch

    def tokenizer(self):
        from research.pretrained.study import tokenizer_for

        return tokenizer_for(self.bundle)

    def processor(self):
        from research.pretrained.runtime import image_processor

        return image_processor(self.bundle)

    def load_composition(self, initialization: Mapping[str, Any], *, torch=None):
        """Load base + delta exactly, then prove the applied scope covers the delta."""
        from research.english.r5_model.parameters import (
            inventory,
            named_trainable,
            select_trainable,
        )
        from research.pretrained.runtime import load_model

        if torch is None:
            import torch as torch_module

            torch = torch_module
        base = Path(initialization["base_checkpoint"])
        delta = Path(initialization["delta_checkpoint"])
        for name, digest in initialization["base_files"].items():
            if digest_file(base / name) != digest:
                raise ValueError("r6_base_checkpoint_changed_before_model_load")
        if digest_file(delta / "state.safetensors") != initialization["delta_sha256"]:
            raise ValueError("r6_delta_changed_before_model_load")

        tokenizer = self.tokenizer()
        model = load_model(base, tokenizer)
        select_trainable(model)
        observed = inventory(named_trainable(model))
        initialization_module.verify_reconstruction_against_inventory(
            observed, metadata=initialization
        )
        tensors = initialization_module.read_delta_tensors(delta / "state.safetensors")
        initialization_module.apply_delta(torch, model, tensors, observed)
        del tensors
        return model, tokenizer, observed
