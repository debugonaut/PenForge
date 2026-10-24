"""Pinned bounded R5 model recipe. Reading it never imports or loads a model."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research.pretrained.artifacts import ROOT, canonical_hash

CONFIG_NAME = "research/configs/english-r5-model.json"


class R5ModelRecipe(BaseModel):
    """The entire bounded R5 experiment design; no hyperparameter search exists."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["english-letters-r5-model-v1"]
    task: Literal["letters"]
    capability_status: Literal["research_only"]
    seed: int = Field(ge=0, le=2**32 - 1)
    class_order: Literal["uppercase_a_to_z_then_lowercase_a_to_z"]

    max_label_tokens: int = Field(ge=4, le=16)
    # Explicit warm start. There is deliberately no fallback to the base bundle.
    initialization: Literal["verified_r4_selected_checkpoint"]
    pass_limit: int = Field(ge=1, le=4)

    trocr_batch_size: int = Field(ge=1, le=8)
    trocr_accumulate_batches: int = Field(ge=1, le=8)
    trocr_learning_rate: float = Field(gt=0, le=0.00005)
    scheduler: Literal["constant_with_warmup_then_linear_decay_v1"]
    warmup_optimizer_steps: int = Field(ge=0, le=100)
    final_learning_rate_ratio: float = Field(ge=0.05, le=1)
    weight_decay: float = Field(ge=0, le=1)
    clip_grad_norm: float = Field(gt=0, le=10)
    early_stopping_patience: int = Field(ge=0, le=2)

    augmentation: Literal["conservative_geometry_only_v1"]
    augmentation_probability: float = Field(ge=0, le=1)
    max_rotation_degrees: float = Field(ge=0, le=10)
    max_shear: float = Field(ge=0, le=0.15)
    min_scale: float = Field(ge=0.85, le=1)
    max_scale: float = Field(ge=1, le=1.15)
    max_translation_fraction: float = Field(ge=0, le=0.06)

    training_preprocessing: Literal["aspect_preserving_white_pad_vit_rgb_384_v1"]
    validation_preprocessing: Literal["pinned_upstream_vit_rgb_resize_384_v1"]
    trocr_objective: Literal["teacher_forced_aligned_token_cross_entropy_v1"]
    decoding: Literal["greedy_no_lexicon_no_language_correction"]
    checkpoint_selection: Literal[
        "injected_validation_exact_accuracy_then_cer_then_latency_then_earlier_pass"
    ]
    test_policy: Literal["part_a_owned_final_test_separately_invoked"]

    max_pass_wall_seconds: int = Field(ge=60, le=3600)
    max_validation_wall_seconds: int = Field(ge=60, le=1800)
    max_run_wall_seconds: int = Field(ge=60, le=21600)
    max_cpu_seconds: int = Field(ge=60, le=36000)
    max_rss_mb: int = Field(ge=2000, le=8000)
    max_output_bytes: int = Field(ge=200_000_000, le=3_000_000_000)

    @model_validator(mode="after")
    def _consistent_bounds(self) -> "R5ModelRecipe":
        if self.min_scale > self.max_scale:
            raise ValueError("r5_min_scale_exceeds_max_scale")
        if self.max_run_wall_seconds < (
            self.max_pass_wall_seconds + self.max_validation_wall_seconds
        ):
            raise ValueError("r5_run_budget_smaller_than_one_pass_plus_validation")
        if self.warmup_optimizer_steps > self.pass_limit * 4096:
            raise ValueError("r5_warmup_exceeds_any_plausible_step_count")
        return self


def load_recipe(path: Path | None = None) -> R5ModelRecipe:
    recipe_path = ROOT / CONFIG_NAME if path is None else path
    recipe = R5ModelRecipe.model_validate_json(recipe_path.read_bytes())
    if recipe.task != "letters":
        raise ValueError("r5_recipe_task_mismatch")
    return recipe


def recipe_hash(recipe: R5ModelRecipe) -> str:
    return canonical_hash(recipe.model_dump())
