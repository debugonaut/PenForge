"""Trace explicit user-assigned photo regions without recognition or invented ink."""

from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from penforge_contracts.schemas import GlyphOutline, ImageRegionAssignment
from penforge_contracts.serialization import content_hash
from PIL import Image, ImageOps

from .geometry import ALGORITHM_VERSION, trace_mask

MANUAL_IMAGE_VERSION = "manual-region-threshold-trace-v1"


@dataclass(frozen=True)
class ManualExtractedGlyph:
    region_id: str
    codepoints: list[int]
    outline: GlyphOutline
    flags: tuple[str, ...]
    crop_png: bytes
    mask_png: bytes
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ManualExtractionResult:
    glyphs: list[ManualExtractedGlyph]
    quality: dict[str, Any]
    reasons: tuple[str, ...]


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=6)
    return output.getvalue()


def _rgb(crop: Image.Image) -> Image.Image:
    # Composite before luma conversion so transparent black cannot become ink.
    if "A" in crop.getbands() or "transparency" in crop.info:
        rgba = crop.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(white, rgba).convert("RGB")
    return crop.convert("RGB")


def extract_manual_regions(
    data: bytes, regions: list[dict[str, Any]], expected_source_hash: str
) -> ManualExtractionResult:
    """Decode once, then trace bounded regions in EXIF-oriented source pixels.

    Labels, baseline and scale are explicit user settings, not model predictions.
    Empty or edge-cut ink cannot produce a candidate. No guide removal, morphology,
    per-letter fit, resizing or missing-character synthesis changes the source.
    """
    started = time.perf_counter()
    if not data or len(data) > 24_000_000:
        raise ValueError("image_byte_budget_exceeded")
    source_hash = "sha256:" + hashlib.sha256(data).hexdigest()
    if source_hash != expected_source_hash:
        raise ValueError("source_hash_mismatch")
    if not isinstance(regions, list) or not 1 <= len(regions) <= 96:
        raise ValueError("manual_image_region_budget_exceeded")
    assignments = [ImageRegionAssignment.model_validate(row) for row in regions]
    if len({row.region_id for row in assignments}) != len(assignments):
        raise ValueError("duplicate_image_region")
    source_settings = {
        (row.sample_id, row.source_sha256, row.source_width, row.source_height, row.units_per_px)
        for row in assignments
    }
    if len(source_settings) != 1:
        raise ValueError("manual_regions_require_one_source_and_scale")
    if any(row.source_sha256 != source_hash for row in assignments):
        raise ValueError("manual_region_source_hash_mismatch")
    crop_pixels = sum(row.rect.width * row.rect.height for row in assignments)
    if crop_pixels > 8_000_000:
        raise ValueError("image_region_batch_budget_exceeded")
    configuration = {
        "image_mode": "manual_regions_v1",
        "image_regions": [row.model_dump(mode="json") for row in assignments],
    }
    quality: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": MANUAL_IMAGE_VERSION,
        "vectorizer_version": ALGORITHM_VERSION,
        "source_bytes_hash": source_hash,
        "source_unchanged": True,
        "configuration": configuration,
        "configuration_hash": content_hash(configuration),
        "coordinate_frame": "exif_transposed_source_pixels",
        "luma_policy": "rgb-white-alpha-integer-299-587-114-strict-less-v1",
        "recognition": "unavailable",
        "ocr": False,
        "identity_source": "user_assignment",
        "expected_text_is_ground_truth": False,
        "source_decode_count": 1,
        "crop_pixels": crop_pixels,
        "regions": [],
    }
    glyphs: list[ManualExtractedGlyph] = []
    reasons: list[str] = []
    with Image.open(io.BytesIO(data)) as source:
        if source.format not in {"PNG", "JPEG", "WEBP"} or getattr(source, "n_frames", 1) != 1:
            raise ValueError("unsupported_analysis_format")
        if source.width * source.height > 16_000_000 or max(source.size) > 6000:
            raise ValueError("analysis_image_budget_exceeded")
        image = ImageOps.exif_transpose(source)
        first = assignments[0]
        if image.size != (first.source_width, first.source_height):
            raise ValueError("manual_region_source_dimensions_mismatch")
        quality["source_width"], quality["source_height"] = image.size
        quality["decode_duration_ms"] = (time.perf_counter() - started) * 1000
        for assignment in assignments:
            row = assignment.model_dump(mode="json")
            rect = assignment.rect
            rgb = _rgb(image.crop((rect.x, rect.y, rect.x + rect.width, rect.y + rect.height)))
            pixels = np.asarray(rgb, dtype=np.uint32)
            gray = (
                299 * pixels[:, :, 0] + 587 * pixels[:, :, 1] + 114 * pixels[:, :, 2] + 500
            ) // 1000
            mask = np.where(gray < assignment.ink_threshold, 255, 0).astype(np.uint8)
            ink = int(np.count_nonzero(mask))
            flags = ["identity_unverified"]
            record: dict[str, Any] = {
                "region_id": assignment.region_id,
                "assignment": row,
                "assignment_hash": content_hash(row),
                "ink_pixels": ink,
                "ink_fraction": ink / mask.size,
                "flags": flags,
                "recognition_score": None,
                "reasons": [],
            }
            quality["regions"].append(record)
            if not ink:
                record["reasons"] = ["empty_ink"]
            elif np.any(mask[0]) or np.any(mask[-1]) or np.any(mask[:, 0]) or np.any(mask[:, -1]):
                record["reasons"] = ["crop_touches_ink"]
                flags.append("possible_guide_or_clipped_ink")
            if record["reasons"]:
                record["status"] = "needs_better_input"
                reasons.extend(record["reasons"])
                continue
            if float(np.median(gray[mask > 0])) > 150:
                flags.append("faint_ink")
            try:
                outline = trace_mask(
                    mask,
                    assignment.codepoints,
                    baseline_px=assignment.baseline_y_px - rect.y,
                    units_per_px=assignment.units_per_px,
                    advance_width_px=rect.width,
                    units_per_em=1000,
                )
            except ValueError:
                record["reasons"] = ["outline_requires_review"]
                record["status"] = "needs_better_input"
                reasons.extend(record["reasons"])
                continue
            provenance = {
                "schema_version": 1,
                "algorithm_version": MANUAL_IMAGE_VERSION,
                "vectorizer_version": ALGORITHM_VERSION,
                "source_bytes_hash": source_hash,
                "source_unchanged": True,
                "source_width": image.width,
                "source_height": image.height,
                "coordinate_frame": quality["coordinate_frame"],
                "luma_policy": quality["luma_policy"],
                "assignment": row,
                "assignment_hash": content_hash(row),
                "outline_hash": content_hash(outline.model_dump(mode="json")),
                "identity_source": "user_assignment",
                "ocr": False,
            }
            glyphs.append(
                ManualExtractedGlyph(
                    assignment.region_id,
                    assignment.codepoints,
                    outline,
                    tuple(flags),
                    _png(rgb),
                    _png(Image.fromarray(mask)),
                    provenance,
                )
            )
            record["status"] = "analyzed"
            record["outline_hash"] = provenance["outline_hash"]
        image.close()
    quality["status"] = "needs_better_input" if reasons else "analyzed"
    quality["candidate_count"] = len(glyphs)
    quality["duration_ms"] = (time.perf_counter() - started) * 1000
    return ManualExtractionResult(glyphs, quality, tuple(dict.fromkeys(reasons)))
