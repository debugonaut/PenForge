"""Track A: pinned marker rectification and authoritative-cell evidence extraction.

This module has no recognizer. Destination codepoints are template context and every
nonempty cell remains identity_unverified until an owner reviews actual geometry.
"""

from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from penforge_contracts.schemas import GlyphOutline, TemplateManifest
from penforge_contracts.serialization import content_hash
from PIL import Image

from .geometry import ALGORITHM_VERSION, trace_mask
from .templates import PIXELS_PER_MM, template_hash

IMAGE_VERSION = "pinned-template-color-dropout-v2"


@dataclass(frozen=True)
class ExtractedGlyph:
    cell_id: str
    codepoints: list[int]
    outline: GlyphOutline
    flags: tuple[str, ...]
    crop_png: bytes
    mask_png: bytes


@dataclass(frozen=True)
class ExtractionResult:
    glyphs: list[ExtractedGlyph]
    quality: dict[str, Any]
    normalized_png: bytes | None
    reasons: tuple[str, ...]


def png_bytes(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("image_encoding_failed")
    return bytes(encoded)


def _measure(image: np.ndarray) -> dict[str, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return {
        "laplacian_variance": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        "gray_p05": float(np.percentile(gray, 5)),
        "gray_p95": float(np.percentile(gray, 95)),
        "mean_gray": float(gray.mean()),
        "width_px": float(image.shape[1]),
        "height_px": float(image.shape[0]),
    }


def extract_image(
    data: bytes,
    manifest: TemplateManifest | dict[str, Any],
    expected_manifest_hash: str,
    config: dict[str, Any] | None = None,
) -> ExtractionResult:
    """Extract one bounded *already verified* image; preserve original bytes/crops.

    Supply verified source bytes inside the isolated analysis process, retaining
    original resolution. The 1,000px Phase03 thumbnail is for display only.
    No provider/network/DB access exists here. Invalid pinned template/hash is a hard error; inadequate
    capture assessment completes successfully with actionable retake reasons.
    """
    started = time.perf_counter()
    manifest = (
        manifest
        if isinstance(manifest, TemplateManifest)
        else TemplateManifest.model_validate(manifest)
    )
    digest = template_hash(manifest)
    if digest != expected_manifest_hash:
        raise ValueError("template_manifest_hash_mismatch")
    if (
        manifest.printing is None
        or manifest.markers.quiet_zone_mm is None
        or manifest.page_identity.rect_mm is None
    ):
        raise ValueError("template_geometry_unpinned")
    config = dict(config or {})
    allowed = {"pixels_per_mm", "trace_tolerance_px", "curve_tolerance_px", "quality_gates"}
    if set(config) - allowed:
        raise ValueError("unsupported_extraction_configuration")
    config = {
        "pixels_per_mm": PIXELS_PER_MM,
        "trace_tolerance_px": 0.0,
        "curve_tolerance_px": 0.0,
        **config,
    }
    scale = float(config.get("pixels_per_mm", PIXELS_PER_MM))
    if not 4 <= scale <= 12:
        raise ValueError("invalid_template_raster_scale")
    if (
        round(manifest.page.width_mm * scale) * round(manifest.page.height_mm * scale) > 16_000_000
        or max(manifest.page.width_mm * scale, manifest.page.height_mm * scale) > 6000
    ):
        raise ValueError("analysis_image_budget_exceeded")
    if len(data) > 24_000_000:
        raise ValueError("image_byte_budget_exceeded")
    # The worker must isolate this entire call as well as Phase03 verification.
    # Recheck dimensions before materializing pixels; never upscale its thumbnail.
    with Image.open(io.BytesIO(data)) as header:
        if header.width * header.height > 16_000_000 or max(header.size) > 6000:
            raise ValueError("analysis_image_budget_exceeded")
        if header.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("unsupported_analysis_format")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("analysis_decode_failed")
    quality: dict[str, Any] = {
        "schema_version": 1,
        "algorithm_version": IMAGE_VERSION,
        "vectorizer_version": ALGORITHM_VERSION,
        "source_bytes_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
        "template_manifest_hash": digest,
        "configuration": config,
        "configuration_hash": content_hash(config),
        "status": "analyzed",
        "recognition": "unavailable",
        "expected_text_is_ground_truth": False,
        "before": _measure(image),
        "cells": [],
        "numeric_quality_gate_source": None,
    }

    def retake(*reasons: str) -> ExtractionResult:
        quality["status"] = "needs_better_input"
        quality["reasons"] = list(reasons)
        quality["duration_ms"] = (time.perf_counter() - started) * 1000
        return ExtractionResult([], quality, None, tuple(reasons))

    gates = config.get("quality_gates")
    if gates is not None:
        if not isinstance(gates, dict) or not isinstance(gates.get("evidence_hash"), str):
            raise ValueError("quality_gates_require_measured_evidence")
        quality["numeric_quality_gate_source"] = gates["evidence_hash"]
        if quality["before"]["laplacian_variance"] < gates.get("minimum_laplacian_variance", 0):
            return retake("too_blurry")

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parameters = cv2.aruco.DetectorParameters()
    parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    corners, ids, _ = cv2.aruco.ArucoDetector(dictionary, parameters).detectMarkers(image)
    if ids is None:
        return retake("page_not_found")
    detected = [int(value) for value in ids.reshape(-1)]
    expected = [item.marker_id for item in manifest.markers.items]
    quality["detected_marker_ids"] = detected
    if len(detected) != len(set(detected)):
        return retake("duplicate_markers")
    if set(detected) != set(expected):
        return retake(
            "template_marker_mismatch" if set(detected) - set(expected) else "page_not_found"
        )
    source: list[Any] = []
    target: list[list[float]] = []
    lookup = {marker.marker_id: marker for marker in manifest.markers.items}
    for marker_id, detected_corners in zip(detected, corners, strict=True):
        rect = lookup[marker_id].rect_mm
        source.extend(detected_corners.reshape(4, 2))
        target.extend(
            [
                [rect.x * scale, rect.y * scale],
                [(rect.x + rect.width) * scale - 1, rect.y * scale],
                [(rect.x + rect.width) * scale - 1, (rect.y + rect.height) * scale - 1],
                [rect.x * scale, (rect.y + rect.height) * scale - 1],
            ]
        )
    matrix, inliers = cv2.findHomography(np.array(source), np.array(target), method=0)
    if matrix is None or not np.isfinite(matrix).all():
        return retake("page_not_found")
    projected = cv2.perspectiveTransform(
        np.array(source, dtype=np.float32).reshape(1, -1, 2), matrix
    ).reshape(-1, 2)
    error = float(np.sqrt(np.mean(np.sum((projected - np.array(target)) ** 2, axis=1))))
    quality["marker_reprojection_rms_px"] = error
    # Geometric inconsistency, e.g. swapped markers, cannot be silently warped.
    if error > max(2.0, scale * 0.5):
        return retake("marker_order_mismatch")
    width = round(manifest.page.width_mm * scale)
    height = round(manifest.page.height_mm * scale)
    warped = cv2.warpPerspective(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255)
    )
    payload, _, _ = cv2.QRCodeDetector().detectAndDecode(warped)
    quality["page_identity_verified"] = payload == manifest.page_identity.public_payload
    if not payload:
        return retake("page_identity_unreadable")
    if payload != manifest.page_identity.public_payload:
        return retake("template_page_mismatch")
    quality["after_rectification"] = _measure(warped)
    # Effective capture scale, before the normalized raster creates extra pixels.
    source_quad = np.array(source, dtype=np.float64)
    span = float(np.linalg.norm(source_quad[1] - source_quad[0]))
    quality["effective_pixels_per_mm"] = span / lookup[detected[0]].rect_mm.width
    if gates is not None:
        if quality["effective_pixels_per_mm"] < gates.get("minimum_pixels_per_mm", 0):
            return retake("low_resolution")
    guides = {guide.guide_id: guide for guide in manifest.guides}
    glyphs = []
    for cell in manifest.cells:
        rect = cell.rect_mm
        x0, y0 = round(rect.x * scale), round(rect.y * scale)
        cw, ch = round(rect.width * scale), round(rect.height * scale)
        crop = warped[y0 : y0 + ch, x0 : x0 + cw].copy()
        # Dark neutral ink survives; pale blue guides drop out without inpainting.
        # Normalize illumination from the local maximum channel, avoiding erasure of
        # dark ink crossing a guide. Blue pen and monochrome photocopies are unqualified.
        b, g, r = [channel.astype(np.float32) for channel in cv2.split(crop)]
        neutral = np.minimum(np.minimum(b, g), r)
        background = cv2.GaussianBlur(np.maximum(np.maximum(b, g), r), (0, 0), max(5, scale * 1.5))
        blue_guide = (b - r > 3) & (b - g > 1) & (b > 100)
        normalized = np.clip(neutral * 255 / np.maximum(background, 1), 0, 255).astype(np.uint8)
        _, mask = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        mask[blue_guide] = 0
        # The manifest crop boundary/printing guides are not ink; color-only removal
        # preserves dark handwriting that touches/crosses that boundary.
        count, _, _, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        ink = int(np.count_nonzero(mask))
        flags = ["identity_unverified"]
        cell_report = {
            "cell_id": cell.cell_id,
            "expected_codepoints": cell.codepoints,
            "ink_pixels": ink,
            "ink_fraction": ink / (cw * ch),
            "components": count - 1,
            "flags": flags,
            "recognition_score": None,
        }
        quality["cells"].append(cell_report)
        if ink == 0:
            flags.append("empty_cell")
            continue
        if np.any(mask[0]) or np.any(mask[-1]) or np.any(mask[:, 0]) or np.any(mask[:, -1]):
            flags.append("border_touch")
        if count > 2:
            flags.append("multi_component")
        if float(np.percentile(neutral[mask > 0], 50)) > 150:
            flags.append("faint_ink")
        guide = guides[cell.guide_id]
        try:
            # Fixed physical frame: cap height=700 units at 14 mm, shared by all cells.
            outline = trace_mask(
                mask,
                cell.codepoints,
                baseline_px=guide.baseline_offset_mm * scale,
                units_per_px=50.0 / scale,
                advance_width_px=cw,
                tolerance_px=float(config["trace_tolerance_px"]),
                curve_tolerance_px=float(config["curve_tolerance_px"]),
            )
        except ValueError as error:
            flags.append(
                str(error)
                if str(error) in {"empty_ink", "outline_node_budget_exceeded"}
                else "outline_requires_review"
            )
            continue
        glyphs.append(
            ExtractedGlyph(
                cell.cell_id,
                cell.codepoints,
                outline,
                tuple(flags),
                png_bytes(crop),
                png_bytes(mask),
            )
        )
    quality["candidate_count"] = len(glyphs)
    quality["empty_cell_count"] = sum("empty_cell" in cell["flags"] for cell in quality["cells"])
    quality["duration_ms"] = (time.perf_counter() - started) * 1000
    quality["normalization"] = {
        "kind": "local_maximum_background_division",
        "dropout": "blue_guides_only",
        "units_per_mm": 50.0,
        "baseline_source": "pinned_manifest",
        "per_glyph_autoscale": False,
    }
    return ExtractionResult(glyphs, quality, png_bytes(warped), ())
