"""Pinned R6 continuation recipe. Reading it never imports or loads a model.

R6 is a bounded continuation of the frozen R5 winner. The recipe is a new experiment,
not a resume: the optimizer is freshly initialised, the schedule covers only R6's own
passes and the data epoch namespace starts after R5's (R6 pass 1 -> data epoch 4, so no
R5 pass is replayed). Every value that could change the model has to change this file and
therefore the recipe hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from research.pretrained.artifacts import ROOT, canonical_hash

CONFIG_NAME = "research/configs/english-r6-model-continuation.json"
CANDIDATE_ID = "r6_continuation_r5_pass3"
MAX_AGGREGATE_PASSES = 3


class R6ModelRecipe(BaseModel):
    """The entire bounded R6 design: one continuation candidate, no hyperparameter search."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["english-letters-r6-model-v1"]
    candidate: Literal["r6_continuation_r5_pass3"]
    task: Literal["letters"]
    capability_status: Literal["research_only"]
    seed: int = Field(ge=0, le=2**32 - 1)
    class_order: Literal["uppercase_a_to_z_then_lowercase_a_to_z"]

    max_label_tokens: int = Field(ge=4, le=16)
    # Explicit composition. There is deliberately no fallback to pretrained or R4 alone.
    initialization: Literal["verified_r4_base_plus_r5_selected_delta"]
    optimizer_initialization: Literal["fresh_adamw_no_r5_optimizer_state"]
    pass_limit: int = Field(ge=1, le=MAX_AGGREGATE_PASSES)
    data_epoch_offset: int = Field(ge=0, le=32)

    trocr_batch_size: int = Field(ge=1, le=8)
    trocr_accumulate_batches: int = Field(ge=1, le=8)
    trocr_learning_rate: float = Field(gt=0, le=0.00002)
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
        "validation_exact_accuracy_then_scalar_cer_then_measured_latency_then_candidate_id"
    ]
    test_policy: Literal["part_a_owned_final_test_separately_invoked"]

    max_pass_wall_seconds: int = Field(ge=60, le=3600)
    max_validation_wall_seconds: int = Field(ge=60, le=1800)
    max_run_wall_seconds: int = Field(ge=60, le=16200)
    max_cpu_seconds: int = Field(ge=60, le=32400)
    max_rss_mb: int = Field(ge=2000, le=6000)
    max_output_bytes: int = Field(ge=200_000_000, le=3_000_000_000)
    interrupted_attempt_charge_seconds: int = Field(ge=0, le=7200)

    @model_validator(mode="after")
    def _consistent_bounds(self) -> "R6ModelRecipe":
        if self.min_scale > self.max_scale:
            raise ValueError("r6_min_scale_exceeds_max_scale")
        if self.max_run_wall_seconds < (
            self.max_pass_wall_seconds + self.max_validation_wall_seconds
        ):
            raise ValueError("r6_run_budget_smaller_than_one_pass_plus_validation")
        if self.warmup_optimizer_steps > self.pass_limit * 4096:
            raise ValueError("r6_warmup_exceeds_any_plausible_step_count")
        if self.interrupted_attempt_charge_seconds < self.max_pass_wall_seconds:
            # A discarded pass cannot be charged less than its own ceiling.
            raise ValueError("r6_interrupted_charge_below_pass_ceiling")
        return self


def load_recipe(path: Path | None = None) -> R6ModelRecipe:
    recipe_path = ROOT / CONFIG_NAME if path is None else path
    recipe = R6ModelRecipe.model_validate_json(recipe_path.read_bytes())
    if recipe.task != "letters":
        raise ValueError("r6_recipe_task_mismatch")
    return recipe


def recipe_hash(recipe: R6ModelRecipe) -> str:
    return canonical_hash(recipe.model_dump())


def data_epoch(recipe: R6ModelRecipe, pass_index: int) -> int:
    """Deterministic data-order/augmentation namespace for one R6 pass.

    R5 used ``pass_index`` 1..3 as its epoch; R6 starts after that so a continuation pass
    never replays an R5 order or an R5 augmentation draw while keeping the pinned seed.
    """
    if not 1 <= pass_index <= recipe.pass_limit:
        raise ValueError("r6_data_epoch_pass_index_outside_recipe")
    return recipe.data_epoch_offset + pass_index
