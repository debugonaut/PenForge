"""Experimental, source-backed subpixel outline proposals; never approves or compiles.

This is a bounded diagnostic for reviewed masks, not a qualified photo extractor.
Threshold offsets are explicit hypotheses about the ink edge, not observed pen widths.
Original masks, frames and outlines stay immutable. Rejected proposals return a copy
of the original outline with the reason recorded for review.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np
from penforge_contracts import GlyphOutline, content_hash

from packages.extraction.geometry import rasterize_outline, trace_mask

ALGORITHM = "reviewed-source-subpixel-v1"


@dataclass(frozen=True)
class Frame:
    baseline_px: float
    units_per_px: float
    origin_x_px: float

    def __post_init__(self) -> None:
        if (
            not all(
                type(v) in {int, float} and math.isfinite(v)
                for v in (self.baseline_px, self.units_per_px, self.origin_x_px)
            )
            or not 0 < self.units_per_px <= 100
        ):
            raise ValueError("invalid_polish_frame")


@dataclass(frozen=True)
class Policy:
    supersample: int = 8
    blur_sigma_px: float = 0.35
    ink_margin_offset: float = 0.12
    max_area_change: float = 0.12
    max_area_growth: float = 0.03
    max_sampled_edge_move_px: float = 1.25

    def __post_init__(self) -> None:
        if type(self.supersample) is not int or self.supersample not in {4, 8}:
            raise ValueError("invalid_polish_policy")
        limits = (
            (self.blur_sigma_px, 0, 0.5),
            (self.ink_margin_offset, 0, 0.5),
            (self.max_area_change, 0, 0.25),
            (self.max_area_growth, 0, 0.03),
            (self.max_sampled_edge_move_px, 0, 1.5),
        )
        if any(
            type(v) not in {int, float} or not math.isfinite(v) or not low <= v <= high
            for v, low, high in limits
        ):
            raise ValueError("invalid_polish_policy")


def ink_margin(
    rgb: np.ndarray,
    *,
    method: str,
    threshold: float,
    blue_gate: float | None = None,
) -> np.ndarray:
    """Keep the original grey/chroma threshold recipe as an explicit signed field.

    Units of 20 grey levels / .04 chroma normalize the two page-specific signals.
    They do not estimate ink opacity or physical stroke width. No source pixels change.
    """
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("invalid_polish_rgb")
    if type(threshold) not in {int, float} or not math.isfinite(threshold):
        raise ValueError("invalid_polish_threshold")
    raw = rgb.astype(np.float32)
    chroma = (raw[:, :, 2] - raw[:, :, 0]) / np.maximum(raw.sum(axis=2), 1)
    if method in {"gray_otsu", "gray_otsu_blue_gate", "manual_detail_box_gray_otsu"}:
        if not 0 <= threshold <= 255:
            raise ValueError("invalid_polish_threshold")
        margin = (threshold - cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)) / 20
    elif method == "blue_chroma_otsu":
        if not 0 <= threshold <= 255 / 1024:
            raise ValueError("invalid_polish_threshold")
        margin = (chroma - threshold) / 0.04
    else:
        raise ValueError("unsupported_polish_mask_method")
    if method == "gray_otsu_blue_gate" and blue_gate is None:
        raise ValueError("polish_blue_gate_required")
    if blue_gate is not None:
        if type(blue_gate) not in {int, float} or not math.isfinite(blue_gate):
            raise ValueError("invalid_polish_blue_gate")
        margin = np.minimum(margin, (chroma - blue_gate) / 0.04)
    return np.clip(margin, -2, 2).astype(np.float32)


def topology(mask: np.ndarray) -> tuple[int, int]:
    """8-connected ink / dual 4-connected paper, including the exterior paper region."""
    ink = (mask > 0).astype(np.uint8)
    components = cv2.connectedComponents(ink, connectivity=8)[0] - 1
    paper = np.pad(1 - ink, 1, constant_values=1)
    holes = cv2.connectedComponents(paper, connectivity=4)[0] - 2
    return components, holes


def render_outline(
    outline: GlyphOutline,
    shape: tuple[int, int],
    frame: Frame,
    *,
    factor: int = 8,
    padding: int = 2,
) -> np.ndarray:
    """Reference geometry sampling on the same pixel-centre frame; no font hinting."""
    if type(factor) is not int or factor not in {1, 2, 4, 8}:
        raise ValueError("invalid_polish_render_factor")
    if type(padding) is not int or not 0 <= padding <= 4:
        raise ValueError("invalid_polish_render_padding")
    offset = (factor - 1) / 2
    return rasterize_outline(
        outline,
        ((shape[0] + padding * 2) * factor, (shape[1] + padding * 2) * factor),
        baseline_px=(frame.baseline_px + padding) * factor + offset,
        units_per_px=frame.units_per_px / factor,
        origin_x_px=(frame.origin_x_px + padding) * factor + offset,
    )


def _edge_distance(a: np.ndarray, b: np.ndarray, factor: int) -> float:
    edges = []
    for mask in (a, b):
        ink = (mask > 0).astype(np.uint8)
        edges.append(ink - cv2.erode(ink, np.ones((3, 3), np.uint8)))
    distances = []
    for source, target in (edges, edges[::-1]):
        distance = cv2.distanceTransform(1 - source, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        distances.append(float(distance[target > 0].max()) / factor)
    return max(distances)


def propose_outline(
    mask: np.ndarray,
    margin: np.ndarray,
    reference: GlyphOutline,
    frame: Frame,
    policy: Policy = Policy(),
) -> tuple[GlyphOutline, dict[str, Any]]:
    """Propose a pending reconstruction, rejecting changed topology/large geometry drift.

    A one-pixel support band limits interpolation to reviewed components. It does not
    automatically remove notebook rules or recover missing/clipped source strokes.
    The original common scale, baseline and advance remain; bearings follow the new edge.
    """
    if (
        mask.dtype != np.uint8
        or mask.ndim != 2
        or mask.size > 65536
        or min(mask.shape, default=0) < 1
        or max(mask.shape, default=0) > 512
        or not np.isin(mask, [0, 255]).all()
        or not np.any(mask)
    ):
        raise ValueError("invalid_polish_mask")
    if margin.shape != mask.shape or not np.issubdtype(margin.dtype, np.floating):
        raise ValueError("invalid_polish_margin")
    if not np.isfinite(margin).all():
        raise ValueError("invalid_polish_margin")
    # Verify that the caller supplied the matching immutable source frame/outline.
    exact = trace_mask(
        mask,
        reference.codepoints,
        baseline_px=frame.baseline_px,
        units_per_px=frame.units_per_px,
        origin_x_px=frame.origin_x_px,
        units_per_em=reference.units_per_em,
        advance_width_px=reference.metrics.advance_width / frame.units_per_px,
    )
    if content_hash(exact.model_dump(mode="json")) != content_hash(
        reference.model_dump(mode="json")
    ):
        raise ValueError("polish_source_frame_mismatch")
    factor, padding = policy.supersample, 2
    support = cv2.dilate(mask, np.ones((3, 3), np.uint8)) > 0
    field: np.ndarray = np.where(support, np.clip(margin, -2, 2), -2).astype(np.float32)
    field = cv2.resize(
        np.pad(field, padding, constant_values=-2),
        None,
        fx=factor,
        fy=factor,
        interpolation=cv2.INTER_CUBIC,
    )
    if policy.blur_sigma_px:
        field = cv2.GaussianBlur(field, (0, 0), policy.blur_sigma_px * factor)
    high = np.where(field >= policy.ink_margin_offset, 255, 0).astype(np.uint8)
    report: dict[str, Any] = {
        "algorithm": ALGORITHM,
        "policy": asdict(policy),
        "frame": asdict(frame),
        "parent_outline_hash": content_hash(reference.model_dump(mode="json")),
        "candidate_origin": "geometric_reconstruction",
        "approval": "pending",
        "writer_fidelity": None,
        "source_topology": topology(mask),
    }

    def retain(reason: str) -> tuple[GlyphOutline, dict[str, Any]]:
        report.update(status="retained_original", reason=reason)
        return reference.model_copy(deep=True), report

    if not np.any(high):
        return retain("empty_proposal")
    candidate = trace_mask(
        high,
        reference.codepoints,
        baseline_px=(frame.baseline_px + padding) * factor + (factor - 1) / 2,
        units_per_px=frame.units_per_px / factor,
        origin_x_px=(frame.origin_x_px + padding) * factor + (factor - 1) / 2,
        units_per_em=reference.units_per_em,
        advance_width_px=reference.metrics.advance_width / (frame.units_per_px / factor),
        tolerance_px=1,
        curve_tolerance_px=1,
    )
    sampled = render_outline(candidate, mask.shape, frame, factor=factor, padding=padding)
    baseline = np.repeat(np.repeat(np.pad(mask, padding), factor, axis=0), factor, axis=1)
    report["proposed_topology"] = topology(sampled)
    report["contours_before_after"] = [len(reference.contours), len(candidate.contours)]
    if report["proposed_topology"] != report["source_topology"] or len(reference.contours) != len(
        candidate.contours
    ):
        return retain("topology_changed")
    area_ratio = float(np.count_nonzero(sampled) / np.count_nonzero(baseline))
    edge_move = _edge_distance(baseline, sampled, factor)
    report.update(
        sampled_area_ratio=area_ratio,
        sampled_symmetric_edge_distance_px=edge_move,
        bearing_delta_units=candidate.metrics.left_side_bearing
        - reference.metrics.left_side_bearing,
    )
    if abs(area_ratio - 1) > policy.max_area_change or area_ratio - 1 > policy.max_area_growth:
        return retain("ink_area_change_exceeded")
    if edge_move > policy.max_sampled_edge_move_px:
        return retain("edge_movement_exceeded")
    report.update(
        status="proposed",
        outline_hash=content_hash(candidate.model_dump(mode="json")),
        caveats=[
            "sampled_not_analytic_bounds",
            "source_blur_not_recovered",
            "owner_review_required",
        ],
    )
    return candidate, report
