"""Default owner-run runtime adapter. Importing it loads no model and no framework."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from research.english.r5_model.recipe import R5ModelRecipe


class ResearchRuntime:
    """Thin adapter over the pinned bundle, tokenizer, processor and strict loader."""

    def __init__(self, bundle: Path) -> None:
        self.bundle = bundle

    def setup(self, recipe: R5ModelRecipe):
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

    def load_initial_model(self, checkpoint: Path, tokenizer: Any):
        # Strict key loading inside load_model is the weight-lineage check; the
        # checkpoint file inventory and hashes are verified before this call.
        from research.pretrained.runtime import load_model

        return load_model(checkpoint, tokenizer)
