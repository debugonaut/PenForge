"""Fixed-round canvas ink to curves, without raster tracing or recognition.

New v2 analysis jobs opt in. The legacy raster converter is kept byte-compatible.
The original normalized stroke document and its observed metadata are never edited.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pathops
from penforge_contracts import GlyphOutline, StrokeDocument, content_hash
from penforge_contracts.canvas import canvas_policy, drawing_provenance

from .geometry import CaptureToFont


def vectorize_round_strokes(
    document: StrokeDocument | dict[str, Any], codepoints: list[int], mapping: Any
) -> tuple[GlyphOutline, dict[str, Any]]:
    policy = canvas_policy()
    if pathops.__version__ != policy["pathops_version"]:
        raise ValueError("drawing_toolchain_mismatch")
    doc = (
        document
        if isinstance(document, StrokeDocument)
        else StrokeDocument.model_validate(document)
    )
    transform = CaptureToFont.parse(mapping)
    size = doc.capture_size_px
    if size is None:
        raise ValueError("capture_dimensions_required")
    if size.width * size.height > policy["max_capture_area_px"]:
        raise ValueError("capture_raster_budget_exceeded")
    count = sum(len(stroke.points) for stroke in doc.strokes)
    if count > policy["max_source_points"]:
        raise ValueError("stroke_geometry_budget_exceeded")
    if codepoints == [32]:
        raise ValueError("space_requires_metrics_only")
    raw_hash = content_hash(doc.model_dump(mode="json"))
    offsets = {stroke.stroke_id: [0.0, 0.0] for stroke in doc.strokes}
    for operation in doc.operations:
        for stroke_id in operation.stroke_ids:
            offsets[stroke_id][0] += operation.dx
            offsets[stroke_id][1] += operation.dy
    ink = pathops.Path()
    for stroke in doc.strokes:
        dx, dy = offsets[stroke.stroke_id]
        points = [
            ((point.x + dx) * size.width, (point.y + dy) * size.height) for point in stroke.points
        ]
        if any(not 0 <= x <= size.width or not 0 <= y <= size.height for x, y in points):
            raise ValueError("derived_stroke_outside_capture")
        ink.moveTo(*points[0])
        for position in points[1:]:
            ink.lineTo(*position)
        # A degenerate segment with round caps produces the same circular dot as
        # the browser. Move-only paths would disappear in Skia's stroker.
        if len(points) == 1:
            ink.lineTo(*points[0])
    ink.stroke(transform.pen_width_px, pathops.LineCap.ROUND_CAP, pathops.LineJoin.ROUND_JOIN, 4)
    ink.convertConicsToQuads(policy["conic_tolerance_capture_px"])
    bounds = ink.bounds
    border_touch = (
        bounds[0] <= 0 or bounds[1] <= 0 or bounds[2] >= size.width or bounds[3] >= size.height
    )
    clip = pathops.Path()
    clip.moveTo(0, 0)
    clip.lineTo(size.width, 0)
    clip.lineTo(size.width, size.height)
    clip.lineTo(0, size.height)
    clip.close()
    # Canvas clips to its artboard; match that, retain a review flag for clipping.
    ink = pathops.op(
        ink, clip, pathops.PathOp.INTERSECTION, fix_winding=True, keep_starting_points=False
    )
    ink.convertConicsToQuads(policy["conic_tolerance_capture_px"])
    if not ink.verbs:
        raise ValueError("empty_ink")

    def point(value):
        x, y = value
        return {
            "x": round((x - transform.origin_x_px) * transform.units_per_px, 6),
            "y": round((transform.baseline_y * size.height - y) * transform.units_per_px, 6),
        }

    contours, contour = [], None
    segments = 0
    for verb, points in ink:
        if verb == pathops.PathVerb.MOVE:
            contour = {"closed": True, "start": point(points[0]), "segments": []}
        elif verb == pathops.PathVerb.CLOSE:
            if contour is None:
                raise ValueError("invalid_round_outline")
            if contour["segments"] and contour["segments"][-1]["to"] != contour["start"]:
                contour["segments"].append({"type": "line", "to": contour["start"]})
                segments += 1
            contours.append(contour)
            contour = None
        else:
            if contour is None:
                raise ValueError("invalid_round_outline")
            segment = {"to": point(points[-1])}
            if verb == pathops.PathVerb.LINE:
                segment["type"] = "line"
            elif verb == pathops.PathVerb.QUAD:
                segment.update(type="quadratic", c=point(points[0]))
            elif verb == pathops.PathVerb.CUBIC:
                segment.update(type="cubic", c1=point(points[0]), c2=point(points[1]))
            else:
                raise ValueError("unsupported_round_curve")
            contour["segments"].append(segment)
            segments += 1
        if segments > policy["max_output_segments"]:
            raise ValueError("stroke_geometry_budget_exceeded")
    if contour is not None or not contours:
        raise ValueError("invalid_round_outline")
    outline = GlyphOutline.model_validate(
        {
            "schema_version": 1,
            "units_per_em": transform.units_per_em,
            "coordinate_system": {"origin": "baseline", "x_positive": "right", "y_positive": "up"},
            "codepoints": codepoints,
            "fill_rule": "evenodd",
            "mapping": "cmap" if len(codepoints) == 1 else "gsub_only",
            "metrics": {
                "advance_width": size.width * transform.units_per_px,
                "left_side_bearing": (ink.bounds[0] - transform.origin_x_px)
                * transform.units_per_px,
            },
            "contours": contours,
        }
    )
    return outline, {
        **drawing_provenance("fixed_round_v1"),
        "source_document_hash": raw_hash,
        "capture_transform": asdict(transform),
        "conic_tolerance_capture_px": policy["conic_tolerance_capture_px"],
        "pen_width_source": "explicit_brush_configuration",
        "raster_supersampling": None,
        "image_segmentation": False,
        "ocr": False,
        "timing": doc.timing,
        "source_point_count": count,
        "output_segment_count": segments,
        "outline_hash": content_hash(outline.model_dump(mode="json")),
        "source_unchanged": raw_hash == content_hash(doc.model_dump(mode="json")),
        "flags": ["border_touch"] if border_touch else [],
    }
