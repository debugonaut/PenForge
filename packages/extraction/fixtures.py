"""Licensed synthetic fixtures only; never presented as real handwriting captures."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from penforge_contracts.schemas import TemplateManifest
from PIL import Image, ImageDraw, ImageFont

from .images import png_bytes
from .templates import PIXELS_PER_MM, template_png

FONT_HASH = "891951df5e8b5b0f0bc760d31375405e795c666b5add70437de12d4a6482b33f"
LICENSE_HASH = "163a2b400e16916ad3196296c946c526d0efce6baf22a2164269ffc62fb9f671"
ROOT = Path(__file__).resolve().parents[2]


def synthetic_sheet(
    manifest: TemplateManifest,
    *,
    font_size_px: int = 105,
    missing_codepoints: tuple[int, ...] = (),
    pixels_per_mm: float = PIXELS_PER_MM,
) -> tuple[bytes, dict[str, np.ndarray]]:
    """Exact raster ground truth from the already licensed/pinned Caveat source."""
    from fontTools.ttLib import TTFont

    font_source = ROOT / "public/fonts/CaveatVariable.woff2"
    license_source = ROOT / "public/fonts/OFL-Caveat.txt"
    if (
        hashlib.sha256(font_source.read_bytes()).hexdigest() != FONT_HASH
        or hashlib.sha256(license_source.read_bytes()).hexdigest() != LICENSE_HASH
    ):
        raise ValueError("synthetic_source_integrity_failed")
    font = TTFont(font_source, recalcTimestamp=False)
    font.flavor = None
    stream = io.BytesIO()
    font.save(stream)
    face = ImageFont.truetype(io.BytesIO(stream.getvalue()), size=font_size_px)
    page = Image.open(io.BytesIO(template_png(manifest, pixels_per_mm=pixels_per_mm))).convert(
        "RGB"
    )
    ground = {}
    guides = {g.guide_id: g for g in manifest.guides}
    for cell in manifest.cells:
        r = cell.rect_mm
        scale = pixels_per_mm
        mask = Image.new("L", (round(r.width * scale), round(r.height * scale)), 0)
        if cell.codepoints[0] not in missing_codepoints:
            draw = ImageDraw.Draw(mask)
            draw.text(
                (round(2 * scale), round(guides[cell.guide_id].baseline_offset_mm * scale)),
                chr(cell.codepoints[0]),
                font=face,
                anchor="ls",
                fill=255,
            )
        ground[cell.cell_id] = np.array(mask)
        ink = Image.new("RGB", mask.size, "#191919")
        page.paste(ink, (round(r.x * scale), round(r.y * scale)), mask)
    buffer = io.BytesIO()
    page.save(buffer, format="PNG", compress_level=6)
    return buffer.getvalue(), ground


def perturb_sheet(data: bytes, condition: dict[str, Any], *, seed: int = 4104) -> bytes:
    """Controlled camera-like perturbations; config/seed determine every byte."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("invalid_synthetic_image")
    h, w = image.shape[:2]
    perspective = float(condition.get("perspective_fraction", 0))
    if perspective:
        src = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
        p = perspective
        dst = np.array(
            [
                [w * p, h * p * 0.5],
                [w * (1 - p * 0.5), h * p],
                [w * (1 - p), h * (1 - p * 0.5)],
                [w * p * 0.5, h * (1 - p)],
            ],
            dtype=np.float32,
        )
        matrix = cv2.getPerspectiveTransform(src, dst)
        image = cv2.warpPerspective(image, matrix, (w, h), borderValue=(255, 255, 255))
    rotation = float(condition.get("rotation_degrees", 0))
    if rotation:
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), rotation, 1)
        image = cv2.warpAffine(image, matrix, (w, h), borderValue=(255, 255, 255))
    lighting = float(condition.get("lighting_gradient", 0))
    if lighting:
        gradient = np.linspace(1 - lighting, 1, w)[None, :, None]
        image = np.clip(image.astype(float) * gradient, 0, 255).astype(np.uint8)
    sigma = float(condition.get("blur_sigma_px", 0))
    if sigma:
        image = cv2.GaussianBlur(image, (0, 0), sigma)
    noise = float(condition.get("noise_sigma", 0))
    if noise:
        image = np.clip(
            image.astype(float) + np.random.default_rng(seed).normal(0, noise, image.shape), 0, 255
        ).astype(np.uint8)
    shrink = float(condition.get("scale", 1))
    if shrink != 1:
        image = cv2.resize(
            image, (round(w * shrink), round(h * shrink)), interpolation=cv2.INTER_AREA
        )
    return png_bytes(image)
