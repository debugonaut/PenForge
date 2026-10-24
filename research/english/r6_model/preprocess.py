"""Typed R6 seam over the immutable R5 preprocessing and augmentation code.

The continuation candidate deliberately keeps R5's image pipeline byte-for-byte: verified
content-addressed crop, bounded affine transform, aspect-preserving white pad to 384, and
the pinned processor's rescale/normalise; validation uses the untouched upstream processor
path. Instead of copying that code, R6 calls the R5 functions and only narrows their recipe
parameter to this package's recipe type, so a future edit to R5 behaviour cannot drift
silently (its files are pinned in the R6 source inventory).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PIL import Image

from research.english.r5_model import augment as _r5_augment
from research.english.r5_model import preprocess as _r5_preprocess
from research.english.r6_model.recipe import R6ModelRecipe

CANVAS = _r5_preprocess.CANVAS
IDENTITY = _r5_augment.IDENTITY
Geometry = _r5_augment.Geometry

__all__ = [
    "CANVAS",
    "IDENTITY",
    "Geometry",
    "apply",
    "aspect_preserving_pad",
    "parameters_for",
    "training_pixels",
    "validation_pixels",
    "verified_image",
]


def verified_image(staging: Path, row: dict[str, Any]) -> Image.Image:
    return _r5_preprocess.verified_image(staging, row)


def aspect_preserving_pad(
    image: Image.Image, *, canvas: int = CANVAS, fill: int = 255
) -> Image.Image:
    return _r5_preprocess.aspect_preserving_pad(image, canvas=canvas, fill=fill)


def parameters_for(recipe: R6ModelRecipe, *, sample_id: str, epoch: int) -> Geometry | None:
    """Deterministic per-sample transform; identical hashing to R5."""
    return _r5_augment.parameters_for(cast(Any, recipe), sample_id=sample_id, epoch=epoch)


def apply(image: Image.Image, geometry: Geometry | None, *, fill: int = 255) -> Image.Image:
    return _r5_augment.apply(image, geometry, fill=fill)


def training_pixels(
    processor: Any,
    staging: Path,
    row: dict[str, Any],
    recipe: R6ModelRecipe,
    *,
    epoch: int,
):
    return _r5_preprocess.training_pixels(processor, staging, row, cast(Any, recipe), epoch=epoch)


def validation_pixels(processor: Any, staging: Path, row: dict[str, Any]):
    return _r5_preprocess.validation_pixels(processor, staging, row)
