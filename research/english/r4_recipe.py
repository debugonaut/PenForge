"""Pinned bounded R4 recipe. Reading it never loads a model."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from research.pretrained.artifacts import ROOT, canonical_hash


class R4Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal["english-letters-r4-v1"]
    task: Literal["letters"]
    capability_status: Literal["research_only"]
    seed: int = Field(ge=0, le=2**32 - 1)
    class_order: Literal["uppercase_a_to_z_then_lowercase_a_to_z"]
    max_label_tokens: int = Field(ge=4, le=16)
    trocr_batch_size: int = Field(ge=1, le=8)
    trocr_accumulate_batches: int = Field(ge=1, le=8)
    trocr_learning_rate: float = Field(gt=0, le=0.0001)
    classifier_batch_size: int = Field(ge=1, le=32)
    classifier_head_learning_rate: float = Field(gt=0, le=0.01)
    classifier_last_block_learning_rate: float = Field(gt=0, le=0.0001)
    weight_decay: float = Field(ge=0, le=1)
    clip_grad_norm: float = Field(gt=0, le=10)
    max_wall_seconds: int = Field(ge=1, le=1800)
    max_cpu_seconds: int = Field(ge=1, le=3600)
    max_rss_mb: int = Field(ge=2000, le=6000)
    max_output_bytes: int = Field(ge=1_400_000_000, le=2_000_000_000)
    image_preprocessing: Literal["pinned_upstream_vit_rgb_resize_384_v1"]
    trocr_objective: Literal["teacher_forced_aligned_token_cross_entropy_v1"]
    classifier_objective: Literal["52_way_cross_entropy_cls_token_v1"]
    decoding: Literal["greedy_no_lexicon_no_language_correction"]
    checkpoint_selection: Literal["validation_exact_accuracy_then_cer_then_latency"]
    test_policy: Literal["one_final_run_after_frozen_validation_winner"]
    pretraining_overlap: Literal["not_independently_audited"]


def load_recipe(path: Path | None = None) -> R4Recipe:
    recipe_path = ROOT / "research/configs/english-r4-letters.json" if path is None else path
    recipe = R4Recipe.model_validate_json(recipe_path.read_bytes())
    if recipe.task != "letters":
        raise ValueError("r4_recipe_task_mismatch")
    return recipe


def recipe_hash(recipe: R4Recipe) -> str:
    return canonical_hash(recipe.model_dump())
