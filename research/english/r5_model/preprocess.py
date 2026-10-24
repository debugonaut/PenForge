"""Deterministic R5 image loading and preprocessing.

Training and validation preprocessing are separate and explicit:

* training: verify the content-addressed crop, optionally apply a bounded affine
  augmentation, scale it into a 384x384 white canvas but preserve aspect ratio, then
  use the pinned processor's rescale/normalize without a second resize;
* validation: the exact pinned upstream processor path used by R3/R4, with no
  augmentation, so validation stays deterministic and comparable.

NIST ``by_write``/``by_class`` crops are already 128x128 normalised thumbnails; the
archives contain no original-resolution page images, so relative letter size and page
baseline are unavailable and are never reconstructed here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from research.english.r5_model import augment as augmentation
from research.english.r5_model.recipe import R5ModelRecipe
from research.pretrained.artifacts import digest_file

CANVAS = 384


def verified_image(staging: Path, row: dict[str, Any]) -> Image.Image:
    """Open a frozen PNG crop after re-verifying its content address."""
    if row.get("path") != f"images/{row.get('hash')}.png":
        raise ValueError("r5_image_reference_not_content_addressed")
    path = staging / row["path"]
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(staging):
        raise ValueError("r5_image_missing_or_unsafe")
    if digest_file(path) != row["hash"]:
        raise ValueError("r5_image_changed_during_run")
    with Image.open(path) as image:
        if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
            raise ValueError("r5_image_format_or_frame_count_changed")
        image.load()
        if image.width <= 0 or image.height <= 0:
            raise ValueError("r5_image_has_no_pixels")
        return image.convert("RGB")


def aspect_preserving_pad(
    image: Image.Image, *, canvas: int = CANVAS, fill: int = 255
) -> Image.Image:
    """Scale to fit inside a square canvas and centre on a white background.

    Upscaling is allowed: the archived crop is the only resolution available and the
    pinned processor also upscales. Aspect ratio is preserved, so no letter is stretched.
    """
    if canvas <= 0:
        raise ValueError("r5_canvas_must_be_positive")
    scale = min(canvas / image.width, canvas / image.height)
    width = max(1, min(canvas, round(image.width * scale)))
    height = max(1, min(canvas, round(image.height * scale)))
    resized = image.resize((width, height), Image.Resampling.BICUBIC)
    padded = Image.new("RGB", (canvas, canvas), (fill, fill, fill))
    padded.paste(resized, ((canvas - width) // 2, (canvas - height) // 2))
    return padded


def _processor_values(processor: Any, image: Image.Image, *, resize: bool):
    size = getattr(processor, "size", None)
    expected = {"height": CANVAS, "width": CANVAS}
    if (
        not isinstance(size, dict)
        or {
            "height": size.get("height"),
            "width": size.get("width"),
        }
        != expected
    ):
        raise ValueError("r5_processor_canvas_changed_from_pinned_384")
    return processor(images=image, return_tensors="pt", do_resize=resize).pixel_values


def training_pixels(
    processor: Any,
    staging: Path,
    row: dict[str, Any],
    recipe: R5ModelRecipe,
    *,
    epoch: int,
):
    """One augmented training tensor of shape ``[1, 3, 384, 384]``."""
    if recipe.training_preprocessing != "aspect_preserving_white_pad_vit_rgb_384_v1":
        raise ValueError("r5_training_preprocessing_mismatch")
    image = verified_image(staging, row)
    geometry = augmentation.parameters_for(recipe, sample_id=row["id"], epoch=epoch)
    image = augmentation.apply(image, geometry)
    padded = aspect_preserving_pad(image)
    return _processor_values(processor, padded, resize=False)


def validation_pixels(processor: Any, staging: Path, row: dict[str, Any]):
    """One unaugmented validation tensor using the pinned upstream processor."""
    image = verified_image(staging, row)
    return _processor_values(processor, image, resize=True)
