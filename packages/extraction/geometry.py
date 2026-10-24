"""Versioned baseline-relative tracing and non-destructive Track C vectorization.

Contours use even-odd fill. A fixed capture-wide scale retains relative proportions;
there is deliberately no bounding-box fit or per-glyph baseline adjustment.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np
from penforge_contracts.schemas import GlyphOutline, StrokeDocument
from penforge_contracts.serialization import content_hash

ALGORITHM_VERSION = "pixel-union-contours-v2"
STROKE_VERSION = "capture-raster-union-v2"


@dataclass(frozen=True)
class CaptureToFont:
    schema_version: int = 1
    units_per_em: int = 1000
    baseline_y: float = 0.75
    units_per_px: float = 2.0
    origin_x_px: float = 0.0
    pen_width_px: float = 3.0

    def __post_init__(self) -> None:
        values = (self.baseline_y, self.units_per_px, self.origin_x_px, self.pen_width_px)
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or not all(type(v) in {int, float} and math.isfinite(v) for v in values)
        ):
            raise ValueError("unsupported_capture_transform")
        if (
            type(self.units_per_em) is not int
            or not 16 <= self.units_per_em <= 16384
            or not 0 <= self.baseline_y <= 1
        ):
            raise ValueError("invalid_capture_transform")
        if (
            not 0 < self.units_per_px <= 100
            or not 0 < self.pen_width_px <= 100
            or not 0 <= self.origin_x_px <= 32768
        ):
            raise ValueError("invalid_capture_transform")

    @classmethod
    def parse(cls, value: Any) -> CaptureToFont:
        if isinstance(value, cls):
            return value
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        return cls(**value)


def _point(x: float, y: float) -> dict[str, float]:
    return {"x": round(float(x), 6), "y": round(float(y), 6)}


def _curves(points: np.ndarray, tolerance: float) -> list[dict[str, Any]]:
    """Fit each adjacent chord with a cubic; accept only a bounded deviation.

    Control vectors are clipped to one-third of the chord. Cubics whose sampled
    distance from their original chord exceeds tolerance retain an exact line.
    This conservative fit is swappable and does not close holes by smoothing.
    """
    segments: list[dict[str, Any]] = []
    for index, p in enumerate(points):
        q = points[(index + 1) % len(points)]
        prev = points[(index - 1) % len(points)]
        nxt = points[(index + 2) % len(points)]
        c1 = p + (q - prev) / 6
        c2 = q - (nxt - p) / 6
        chord = q - p
        length = float(np.linalg.norm(chord))
        if length <= 0:
            continue
        # The convex hull bounds the entire curve distance to the infinite chord.
        cross1 = abs(float(chord[0] * (c1 - p)[1] - chord[1] * (c1 - p)[0])) / length
        cross2 = abs(float(chord[0] * (c2 - p)[1] - chord[1] * (c2 - p)[0])) / length
        projections = [float(np.dot(c - p, chord) / length**2) for c in (c1, c2)]
        if (
            tolerance > 0
            and max(cross1, cross2) <= tolerance
            and all(0 <= projection <= 1 for projection in projections)
        ):
            segments.append(
                {"type": "cubic", "c1": _point(*c1), "c2": _point(*c2), "to": _point(*q)}
            )
        else:
            segments.append({"type": "line", "to": _point(*q)})
    return segments


def _pixel_boundaries(binary: np.ndarray) -> list[np.ndarray]:
    """Exact pixel-square union, retaining tiny islands and holes.

    Edges follow ink on their right in image coordinates. At diagonal contacts
    the rightmost turn keeps touching islands separate instead of crossing paths.
    """
    ink = binary > 0
    padded = np.pad(ink, 1)
    sides = [
        ink & ~padded[:-2, 1:-1],
        ink & ~padded[1:-1, 2:],
        ink & ~padded[2:, 1:-1],
        ink & ~padded[1:-1, :-2],
    ]
    if sum(int(np.count_nonzero(side)) for side in sides) > 262144:
        raise ValueError("outline_boundary_budget_exceeded")
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for side, selected in enumerate(sides):
        rows, cols = np.nonzero(selected)
        for y, x in zip(rows.tolist(), cols.tolist(), strict=True):
            start, end = [
                ((x, y), (x + 1, y)),
                ((x + 1, y), (x + 1, y + 1)),
                ((x + 1, y + 1), (x, y + 1)),
                ((x, y + 1), (x, y)),
            ][side]
            edges.setdefault(start, []).append(end)
    directions = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}
    preference = {1: 0, 0: 1, 3: 2, 2: 3}
    loops = []
    while edges:
        start = next(iter(edges))
        current = start
        direction = 0
        points = []
        while True:
            points.append(current)
            options = edges[current]
            end = min(
                options,
                key=lambda p: preference[
                    (directions[(p[0] - current[0], p[1] - current[1])] - direction) % 4
                ],
            )
            options.remove(end)
            if not options:
                del edges[current]
            direction = directions[(end[0] - current[0], end[1] - current[1])]
            current = end
            if current == start:
                break
        loops.append(np.asarray(points, dtype=np.float32) - 0.5)
    return loops


def trace_mask(
    mask: np.ndarray,
    codepoints: list[int],
    *,
    baseline_px: float,
    units_per_px: float,
    origin_x_px: float = 0,
    units_per_em: int = 1000,
    advance_width_px: float | None = None,
    tolerance_px: float = 0.0,
    curve_tolerance_px: float = 0.0,
) -> GlyphOutline:
    """Trace a bounded binary mask, retaining all nondegenerate contours and holes."""
    if mask.ndim != 2 or mask.size > 16_000_000 or mask.dtype != np.uint8:
        raise ValueError("invalid_trace_mask")
    if not math.isfinite(units_per_px) or units_per_px <= 0:
        raise ValueError("invalid_font_scale")
    if not 0 <= tolerance_px <= 1 or not 0 <= curve_tolerance_px <= 1:
        raise ValueError("invalid_trace_tolerance")
    binary = np.where(mask > 0, 255, 0).astype(np.uint8)
    contours = []
    for boundary in _pixel_boundaries(binary):
        raw = cv2.approxPolyDP(boundary, tolerance_px, True).reshape(-1, 2).astype(float)
        if len(raw) < 3:
            continue
        # Image-edge winding becomes clockwise outer loops/CCW holes in y-up.
        pts = np.column_stack(
            ((raw[:, 0] - origin_x_px) * units_per_px, (baseline_px - raw[:, 1]) * units_per_px)
        )
        segments = _curves(pts, curve_tolerance_px * units_per_px)
        if segments:
            contours.append({"closed": True, "start": _point(*pts[0]), "segments": segments})
    if not contours and codepoints != [32]:
        raise ValueError("empty_ink")
    if codepoints == [32]:
        if np.any(binary):
            raise ValueError("space_requires_metrics_only")
        contours = []
    ink_x = np.where(binary > 0)[1]
    lsb = (float(ink_x.min()) - 0.5 - origin_x_px) * units_per_px if ink_x.size else 0.0
    return GlyphOutline.model_validate(
        {
            "schema_version": 1,
            "units_per_em": units_per_em,
            "coordinate_system": {"origin": "baseline", "x_positive": "right", "y_positive": "up"},
            "codepoints": codepoints,
            "fill_rule": "evenodd",
            "mapping": "cmap" if len(codepoints) == 1 else "gsub_only",
            "metrics": {
                "advance_width": float(advance_width_px or mask.shape[1]) * units_per_px,
                "left_side_bearing": lsb,
            },
            "contours": contours,
        }
    )


def vectorize_strokes(
    document: StrokeDocument | dict[str, Any], codepoints: list[int], mapping: Any
) -> tuple[GlyphOutline, dict[str, Any]]:
    """Track C: capture raster union -> closed outlines, with no image CV/OCR stages.

    Rasterization is a geometric union at 4x capture resolution. Brush width is
    configuration, not invented pressure. Observed pressure scales width only when
    supplied. Original model/bytes and pen metadata are never modified.
    """
    doc = (
        document
        if isinstance(document, StrokeDocument)
        else StrokeDocument.model_validate(document)
    )
    transform = CaptureToFont.parse(mapping)
    size = doc.capture_size_px
    if size is None:
        raise ValueError("capture_dimensions_required")
    if size.width * size.height > 1_000_000:
        raise ValueError("capture_raster_budget_exceeded")
    raw_hash = content_hash(doc.model_dump(mode="json"))
    factor = 4
    mask = np.zeros((size.height * factor, size.width * factor), dtype=np.uint8)
    translated = {stroke.stroke_id: [0.0, 0.0] for stroke in doc.strokes}
    for operation in doc.operations:
        for stroke_id in operation.stroke_ids:
            translated[stroke_id][0] += operation.dx
            translated[stroke_id][1] += operation.dy
    for stroke in doc.strokes:
        dx, dy = translated[stroke.stroke_id]
        values = []
        for point in stroke.points:
            x, y = (point.x + dx) * size.width, (point.y + dy) * size.height
            if not 0 <= x <= size.width or not 0 <= y <= size.height:
                raise ValueError("derived_stroke_outside_capture")
            width = transform.pen_width_px * (
                max(0.1, point.pressure * 2) if point.pressure is not None else 1
            )
            values.append((np.array([x * factor, y * factor]), width * factor))
        for index, (p, width) in enumerate(values):
            cv2.circle(mask, tuple(np.rint(p).astype(int)), max(1, round(width / 2)), 255, -1)
            if index == 0:
                continue
            q, prior_width = values[index - 1]
            distance = float(np.linalg.norm(p - q))
            count = max(1, math.ceil(distance / 2))
            for step in range(count + 1):
                alpha = step / count
                center = q * (1 - alpha) + p * alpha
                radius = (prior_width * (1 - alpha) + width * alpha) / 2
                cv2.circle(mask, tuple(np.rint(center).astype(int)), max(1, round(radius)), 255, -1)
    outline = trace_mask(
        mask,
        codepoints,
        baseline_px=transform.baseline_y * size.height * factor,
        units_per_px=transform.units_per_px / factor,
        origin_x_px=transform.origin_x_px * factor,
        units_per_em=transform.units_per_em,
        advance_width_px=size.width * factor,
    )
    provenance = {
        "algorithm_version": STROKE_VERSION,
        "vectorizer_version": ALGORITHM_VERSION,
        "source_document_hash": raw_hash,
        "capture_transform": asdict(transform),
        "raster_supersampling": factor,
        "tolerance_capture_px": 0.0,
        "raster_grid_step_capture_px": 1.0 / factor,
        "pen_width_source": "explicit_brush_configuration",
        "pressure_policy": "observed_only",
        "image_segmentation": False,
        "ocr": False,
        "timing": doc.timing,
        "outline_hash": content_hash(outline.model_dump(mode="json")),
        "source_unchanged": raw_hash == content_hash(doc.model_dump(mode="json")),
        "flags": ["border_touch"]
        if (np.any(mask[0]) or np.any(mask[-1]) or np.any(mask[:, 0]) or np.any(mask[:, -1]))
        else [],
    }
    return outline, provenance


def outline_svg(outline: GlyphOutline, *, padding: float = 20) -> str:
    points = [
        point
        for contour in outline.contours
        for point in [contour.start, *[s.to for s in contour.segments]]
    ]
    for contour in outline.contours:
        for segment in contour.segments:
            if segment.type == "quadratic":
                points.append(segment.c)
            elif segment.type == "cubic":
                points.extend((segment.c1, segment.c2))
    x0 = min([0.0, *[p.x for p in points]]) - padding
    x1 = max([outline.metrics.advance_width, *[p.x for p in points]]) + padding
    y0 = min([-250.0, *[p.y for p in points]]) - padding
    y1 = max([800.0, *[p.y for p in points]]) + padding
    paths = []
    for contour in outline.contours:
        commands = [f"M {contour.start.x} {-contour.start.y}"]
        for segment in contour.segments:
            if segment.type == "line":
                commands.append(f"L {segment.to.x} {-segment.to.y}")
            elif segment.type == "quadratic":
                commands.append(f"Q {segment.c.x} {-segment.c.y} {segment.to.x} {-segment.to.y}")
            else:
                commands.append(
                    f"C {segment.c1.x} {-segment.c1.y} {segment.c2.x} {-segment.c2.y} {segment.to.x} {-segment.to.y}"
                )
        paths.append(" ".join(commands) + " Z")
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {-y1} {x1 - x0} {y1 - y0}"><path fill="#30251f" fill-rule="evenodd" d="{" ".join(paths)}"/></svg>'


def rasterize_outline(
    outline: GlyphOutline,
    shape: tuple[int, int],
    *,
    baseline_px: float,
    units_per_px: float,
    origin_x_px: float = 0,
) -> np.ndarray:
    """Reference even-odd fill at pixel centers, with adaptive curve flattening.

    This is a binary geometry adapter, not a browser/font raster quality claim.
    Unlike inclusive integer fillPoly edges, a one-pixel square fills one pixel.
    """
    if len(shape) != 2 or min(shape) < 1 or shape[0] * shape[1] > 16_000_000:
        raise ValueError("invalid_raster_shape")
    if not math.isfinite(units_per_px) or units_per_px <= 0:
        raise ValueError("invalid_font_scale")
    result = np.zeros(shape, dtype=np.uint8)
    paths = []
    budget = [0]
    tolerance = units_per_px / 16

    def cubic(p, c1, c2, q, depth=0):
        chord = q - p
        length = float(np.linalg.norm(chord))
        distance = (
            max(float(np.linalg.norm(c1 - p)), float(np.linalg.norm(c2 - p)))
            if length < 1e-12
            else max(
                abs(float(chord[0] * (c - p)[1] - chord[1] * (c - p)[0])) / length for c in (c1, c2)
            )
        )
        projection_ok = (length <= 1e-12 and distance <= tolerance) or (
            length > 1e-12
            and all(0 <= float(np.dot(c - p, chord)) / length**2 <= 1 for c in (c1, c2))
        )
        if (distance <= tolerance and projection_ok) or depth >= 16:
            budget[0] += 1
            if budget[0] > 131072:
                raise ValueError("curve_raster_budget_exceeded")
            return [q]
        a, b, c = (p + c1) / 2, (c1 + c2) / 2, (c2 + q) / 2
        d, e = (a + b) / 2, (b + c) / 2
        middle = (d + e) / 2
        return cubic(p, a, d, middle, depth + 1) + cubic(middle, e, c, q, depth + 1)

    for contour in outline.contours:
        p = np.array([contour.start.x, contour.start.y])
        points = [p]
        for segment in contour.segments:
            q = np.array([segment.to.x, segment.to.y])
            if segment.type == "line":
                points.append(q)
            elif segment.type == "quadratic":
                control = np.array([segment.c.x, segment.c.y])
                points.extend(cubic(p, p + 2 / 3 * (control - p), q + 2 / 3 * (control - q), q))
            else:
                points.extend(
                    cubic(
                        p,
                        np.array([segment.c1.x, segment.c1.y]),
                        np.array([segment.c2.x, segment.c2.y]),
                        q,
                    )
                )
            p = q
        paths.append(
            np.array(
                [
                    [v[0] / units_per_px + origin_x_px, baseline_px - v[1] / units_per_px]
                    for v in points
                ]
            )
        )
    if not paths:
        return result
    starts = np.concatenate([path[:-1] for path in paths])
    ends = np.concatenate([path[1:] for path in paths])
    for y in range(shape[0]):
        crossing = (starts[:, 1] > y) != (ends[:, 1] > y)
        a, b = starts[crossing], ends[crossing]
        if not len(a):
            continue
        intersections = np.sort(a[:, 0] + (y - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1]))
        for left, right in zip(intersections[::2], intersections[1::2], strict=True):
            x0, x1 = max(0, math.ceil(left - 1e-9)), min(shape[1], math.ceil(right - 1e-9))
            if x1 > x0:
                result[y, x0:x1] = 255
    return result
