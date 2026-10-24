"""Deterministic, label-preserving geometry augmentation for R5 training only.

The transform is an affine map about the image centre: small rotation, shear, isotropic
scale and translation. Horizontal/vertical flips, elastic warps, aspect distortion and
colour inversions are deliberately absent because they can change letter identity
(``b``/``d``, ``p``/``q``, ``u``/``n``). Augmentation is sampled from a per-sample seed,
so pass order and resume boundaries cannot change which transform a crop receives.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass

from PIL import Image

from research.english.r5_model.recipe import R5ModelRecipe


@dataclass(frozen=True)
class Geometry:
    angle_degrees: float
    shear: float
    scale: float
    delta_x_fraction: float
    delta_y_fraction: float


IDENTITY = Geometry(0.0, 0.0, 1.0, 0.0, 0.0)


def parameters_for(recipe: R5ModelRecipe, *, sample_id: str, epoch: int) -> Geometry | None:
    """Return a bounded transform, or ``None`` when this sample stays unchanged."""
    if epoch < 1:
        raise ValueError("r5_augmentation_epoch_must_be_at_least_one")
    if not sample_id:
        raise ValueError("r5_augmentation_requires_a_sample_id")
    digest = hashlib.sha256(f"{recipe.seed}:{epoch}:{sample_id}".encode()).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    if recipe.augmentation_probability <= 0 or rng.random() >= recipe.augmentation_probability:
        return None
    return Geometry(
        angle_degrees=rng.uniform(-recipe.max_rotation_degrees, recipe.max_rotation_degrees),
        shear=rng.uniform(-recipe.max_shear, recipe.max_shear),
        scale=rng.uniform(recipe.min_scale, recipe.max_scale),
        delta_x_fraction=rng.uniform(
            -recipe.max_translation_fraction, recipe.max_translation_fraction
        ),
        delta_y_fraction=rng.uniform(
            -recipe.max_translation_fraction, recipe.max_translation_fraction
        ),
    )


def affine_coefficients(
    geometry: Geometry, size: tuple[int, int]
) -> tuple[float, float, float, float, float, float]:
    """PIL ``AFFINE`` coefficients mapping output coordinates to input coordinates."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("r5_augmentation_requires_positive_image_size")
    if geometry.scale <= 0:
        raise ValueError("r5_augmentation_scale_must_be_positive")
    radians = math.radians(geometry.angle_degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    # Forward transform: scale * rotation * shear.
    forward = (
        (geometry.scale * cosine, geometry.scale * (cosine * math.tan(geometry.shear) - sine)),
        (geometry.scale * sine, geometry.scale * (sine * math.tan(geometry.shear) + cosine)),
    )
    determinant = forward[0][0] * forward[1][1] - forward[0][1] * forward[1][0]
    if determinant <= 0:
        raise ValueError("r5_augmentation_must_preserve_orientation")
    inverse = (
        (forward[1][1] / determinant, -forward[0][1] / determinant),
        (-forward[1][0] / determinant, forward[0][0] / determinant),
    )
    centre_x, centre_y = width / 2, height / 2
    shifted_x = centre_x + geometry.delta_x_fraction * width
    shifted_y = centre_y + geometry.delta_y_fraction * height
    coefficient_x = -inverse[0][0] * shifted_x - inverse[0][1] * shifted_y + centre_x
    coefficient_y = -inverse[1][0] * shifted_x - inverse[1][1] * shifted_y + centre_y
    return (
        inverse[0][0],
        inverse[0][1],
        coefficient_x,
        inverse[1][0],
        inverse[1][1],
        coefficient_y,
    )


def apply(image: Image.Image, geometry: Geometry | None, *, fill: int = 255) -> Image.Image:
    """Apply a bounded affine transform without changing the canvas size."""
    if geometry is None or geometry == IDENTITY:
        return image
    if image.mode not in {"L", "RGB"}:
        raise ValueError("r5_augmentation_requires_grayscale_or_rgb")
    fillcolor = fill if image.mode == "L" else (fill, fill, fill)
    return image.transform(
        image.size,
        Image.Transform.AFFINE,
        affine_coefficients(geometry, image.size),
        resample=Image.Resampling.BICUBIC,
        fillcolor=fillcolor,
    )
