"""Deterministic capture-condition augmentation for the volunteer adaptation experiment.

The pilot showed what a real phone photograph of a notebook page looks like: uneven page
lighting, a grey-blue cast, mild blur, JPEG artefacts, slight rotation and perspective,
printed rules and natural margins. This module reproduces that *kind* of variation in a
bounded, label-preserving way for training only.

Rules that keep labels unambiguous, and are enforced here:

* no flips, no elastic warp and no aspect distortion, because they can turn ``b`` into
  ``d`` or ``p`` into ``q``;
* rotation, shear, perspective, scale and translation are small and bounded;
* brightness and illumination changes are photometric only;
* blur and JPEG are mild, and the JPEG round-trip stays at a high quality;
* a synthetic notebook rule is drawn only in a margin band outside the glyph's own ink
  box, so it can never cover a stroke or invent one.

Everything is sampled from a hash of ``(seed, epoch, sample id, component)``, so the same
sample always receives the same transform for the same pass, and a resumed run cannot
silently change it. Importing this module imports no model framework.
"""

from __future__ import annotations

import hashlib
import io
import math
import random
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

CAPTURE_VERSION = "penforge-volunteer-capture-simulation-v2"
IDENTITY_VERSION = "penforge-volunteer-capture-identity-v1"


@dataclass(frozen=True)
class CapturePlan:
    """One bounded, label-preserving capture simulation."""

    rotate_degrees: float
    shear: float
    scale: float
    delta_x_fraction: float
    delta_y_fraction: float
    perspective_fraction: float
    brightness: float
    illumination_amplitude: float
    illumination_direction: float
    blur_radius: float
    jpeg_quality: int | None
    ruled_lines: int

    @property
    def is_identity(self) -> bool:
        return (
            self.rotate_degrees == 0.0
            and self.shear == 0.0
            and self.scale == 1.0
            and self.delta_x_fraction == 0.0
            and self.delta_y_fraction == 0.0
            and self.perspective_fraction == 0.0
            and self.brightness == 1.0
            and self.illumination_amplitude == 0.0
            and self.blur_radius == 0.0
            and self.jpeg_quality is None
            and self.ruled_lines == 0
        )

    def record(self) -> dict[str, Any]:
        return {
            "version": CAPTURE_VERSION,
            "rotate_degrees": round(self.rotate_degrees, 6),
            "shear": round(self.shear, 6),
            "scale": round(self.scale, 6),
            "delta_x_fraction": round(self.delta_x_fraction, 6),
            "delta_y_fraction": round(self.delta_y_fraction, 6),
            "perspective_fraction": round(self.perspective_fraction, 6),
            "brightness": round(self.brightness, 6),
            "illumination_amplitude": round(self.illumination_amplitude, 6),
            "illumination_direction": round(self.illumination_direction, 6),
            "blur_radius": round(self.blur_radius, 6),
            "jpeg_quality": self.jpeg_quality,
            "ruled_lines": self.ruled_lines,
        }


IDENTITY = CapturePlan(0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, None, 0)

PARAMETER_NAMES = (
    "augmentation_probability",
    "max_rotation_degrees",
    "max_shear",
    "min_scale",
    "max_scale",
    "max_translation_fraction",
    "max_perspective_fraction",
    "brightness_min",
    "brightness_max",
    "max_illumination_amplitude",
    "blur_probability",
    "max_blur_radius",
    "jpeg_probability",
    "jpeg_quality_min",
    "jpeg_quality_max",
    "ruled_background_probability",
)


def _rng(capture: Any, *, sample_id: str, epoch: int, component: str) -> random.Random:
    if epoch < 1:
        raise ValueError("volunteer_capture_epoch_must_be_at_least_one")
    if not sample_id:
        raise ValueError("volunteer_capture_requires_a_sample_id")
    digest = hashlib.sha256(
        f"{CAPTURE_VERSION}:{capture.seed}:{epoch}:{sample_id}:{component}".encode()
    ).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def plan_for(capture: Any, *, sample_id: str, epoch: int) -> CapturePlan:
    """Sample one bounded plan; identical inputs always give an identical plan."""
    rng = _rng(capture, sample_id=sample_id, epoch=epoch, component="plan")
    if capture.augmentation_probability <= 0 or rng.random() >= capture.augmentation_probability:
        return IDENTITY
    rotate = rng.uniform(-capture.max_rotation_degrees, capture.max_rotation_degrees)
    shear = rng.uniform(-capture.max_shear, capture.max_shear)
    scale = rng.uniform(capture.min_scale, capture.max_scale)
    delta_x = rng.uniform(-capture.max_translation_fraction, capture.max_translation_fraction)
    delta_y = rng.uniform(-capture.max_translation_fraction, capture.max_translation_fraction)
    perspective = rng.uniform(-capture.max_perspective_fraction, capture.max_perspective_fraction)
    brightness = rng.uniform(capture.brightness_min, capture.brightness_max)
    illumination = rng.uniform(0.0, capture.max_illumination_amplitude)
    direction = rng.uniform(0.0, 2 * math.pi)
    blur = (
        rng.uniform(0.0, capture.max_blur_radius)
        if rng.random() < capture.blur_probability
        else 0.0
    )
    quality = (
        rng.randint(capture.jpeg_quality_min, capture.jpeg_quality_max)
        if rng.random() < capture.jpeg_probability
        else None
    )
    rules = 1 if rng.random() < capture.ruled_background_probability else 0
    return CapturePlan(
        rotate_degrees=rotate,
        shear=shear,
        scale=scale,
        delta_x_fraction=delta_x,
        delta_y_fraction=delta_y,
        perspective_fraction=perspective,
        brightness=brightness,
        illumination_amplitude=illumination,
        illumination_direction=direction,
        blur_radius=blur,
        jpeg_quality=quality,
        ruled_lines=rules,
    )


def affine_coefficients(
    plan: CapturePlan, size: tuple[int, int]
) -> tuple[float, float, float, float, float, float]:
    """PIL ``AFFINE`` coefficients mapping output coordinates back to input ones."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("volunteer_capture_requires_positive_image_size")
    radians = math.radians(plan.rotate_degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    forward = (
        (plan.scale * cosine, plan.scale * (cosine * math.tan(plan.shear) - sine)),
        (plan.scale * sine, plan.scale * (sine * math.tan(plan.shear) + cosine)),
    )
    determinant = forward[0][0] * forward[1][1] - forward[0][1] * forward[1][0]
    if determinant <= 0:
        raise ValueError("volunteer_capture_must_preserve_orientation")
    inverse = (
        (forward[1][1] / determinant, -forward[0][1] / determinant),
        (-forward[1][0] / determinant, forward[0][0] / determinant),
    )
    centre_x, centre_y = width / 2, height / 2
    shifted_x = centre_x + plan.delta_x_fraction * width
    shifted_y = centre_y + plan.delta_y_fraction * height
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


def perspective_coefficients(plan: CapturePlan, size: tuple[int, int]) -> tuple[float, ...]:
    """Pillow's output-to-input map for the specified forward corner displacement."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("volunteer_capture_requires_positive_image_size")
    amount = plan.perspective_fraction
    if amount == 0:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
    dx, dy = amount * width, amount * height
    destination = [
        (-dx, -dy),
        (width + dx, -dy),
        (width + dx, height + dy),
        (-dx, height + dy),
    ]
    source = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    return _solve_perspective(destination, source)


def _solve_perspective(
    source: list[tuple[float, float]], destination: list[tuple[float, float]]
) -> tuple[float, ...]:
    """Solve the 8 unknowns of a projective map with a small Gaussian elimination."""
    if len(source) != 4 or len(destination) != 4:
        raise ValueError("volunteer_capture_perspective_requires_four_points")
    if not all(math.isfinite(value) for point in (*source, *destination) for value in point):
        raise ValueError("volunteer_capture_perspective_requires_finite_points")
    matrix: list[list[float]] = []
    for (sx, sy), (dx, dy) in zip(source, destination, strict=True):
        matrix.append([sx, sy, 1, 0, 0, 0, -dx * sx, -dx * sy, dx])
        matrix.append([0, 0, 0, sx, sy, 1, -dy * sx, -dy * sy, dy])
    size = 8
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(matrix[row][column]))
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        value = matrix[column][column]
        if abs(value) < 1e-12:
            raise ValueError("volunteer_capture_perspective_is_singular")
        for index in range(column, size + 1):
            matrix[column][index] /= value
        for row in range(size):
            if row == column:
                continue
            factor = matrix[row][column]
            if factor == 0:
                continue
            for index in range(column, size + 1):
                matrix[row][index] -= factor * matrix[column][index]
    return tuple(matrix[row][size] for row in range(size))


def _ink_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    grey = image.convert("L")
    width, height = grey.size
    pixels = list(grey.getdata())
    xs: list[int] = []
    ys: list[int] = []
    for index, value in enumerate(pixels):
        if value < 110:
            xs.append(index % width)
            ys.append(index // width)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def _illumination(image: Image.Image, plan: CapturePlan) -> Image.Image:
    if plan.illumination_amplitude == 0:
        return image
    width, height = image.size
    direction = plan.illumination_direction
    dx, dy = math.cos(direction), math.sin(direction)
    channels = []
    amplitude = plan.illumination_amplitude
    for band in image.split():
        values = list(band.getdata())
        for index, value in enumerate(values):
            x = (index % width) / max(1, width - 1)
            y = (index // width) / max(1, height - 1)
            # Centred ramp: bright on one side, darker on the other, mean gain 1.0.
            factor = 1.0 + amplitude * ((x - 0.5) * dx + (y - 0.5) * dy) * 2
            values[index] = max(0, min(255, int(round(value * factor))))
        channel = Image.new("L", image.size)
        channel.putdata(values)
        channels.append(channel)
    return Image.merge("RGB", channels)


def _draw_ruled_line(image: Image.Image, plan: CapturePlan, index: int) -> Image.Image:
    """Draw one synthetic notebook rule in a margin band, never over the glyph."""
    ink = _ink_box(image)
    if ink is None:
        return image
    width, height = image.size
    _, top, _, bottom = ink
    # Prefer the band below the glyph, then the band above it, then whatever is larger.
    bands = [(bottom + 2, height), (0, top - 2)]
    start, end = max(bands, key=lambda band: band[1] - band[0])
    if end - start < 6:
        return image
    position = start + max(3, (end - start) // 2 + index)
    if position >= height - 1:
        return image
    draw = ImageDraw.Draw(image)
    shade = 120 if index % 2 == 0 else 150
    draw.line((0, position, width - 1, position), fill=(shade, shade, shade), width=1)
    return image


def apply(image: Image.Image, plan: CapturePlan) -> Image.Image:
    """Apply one plan: bounded geometry, photometry, a margin rule, blur and JPEG."""
    if plan.is_identity:
        return image.copy()
    if image.mode != "RGB":
        raise ValueError("volunteer_capture_requires_rgb")
    result = image.convert("RGB")
    if plan.perspective_fraction != 0:
        result = result.transform(
            result.size,
            Image.Transform.PERSPECTIVE,
            perspective_coefficients(plan, result.size),
            resample=Image.Resampling.BICUBIC,
            fillcolor=(255, 255, 255),
        )
    if (
        plan.rotate_degrees != 0
        or plan.shear != 0
        or plan.scale != 1.0
        or plan.delta_x_fraction != 0
        or plan.delta_y_fraction != 0
    ):
        result = result.transform(
            result.size,
            Image.Transform.AFFINE,
            affine_coefficients(plan, result.size),
            resample=Image.Resampling.BICUBIC,
            fillcolor=(255, 255, 255),
        )
    result = _illumination(result, plan)
    if plan.brightness != 1.0:
        result = ImageEnhance.Brightness(result).enhance(plan.brightness)
    for index in range(plan.ruled_lines):
        result = _draw_ruled_line(result, plan, index)
    if plan.blur_radius > 0:
        result = result.filter(ImageFilter.GaussianBlur(radius=plan.blur_radius))
    if plan.jpeg_quality is not None:
        buffer = io.BytesIO()
        result.save(buffer, format="JPEG", quality=plan.jpeg_quality, subsampling=0)
        buffer.seek(0)
        with Image.open(buffer) as decoded:
            decoded.load()
            result = decoded.convert("RGB")
    return result


__all__ = [
    "CAPTURE_VERSION",
    "CapturePlan",
    "IDENTITY",
    "IDENTITY_VERSION",
    "PARAMETER_NAMES",
    "affine_coefficients",
    "apply",
    "perspective_coefficients",
    "plan_for",
]
