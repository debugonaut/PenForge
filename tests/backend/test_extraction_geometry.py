"""Faithfulness checks for canonical geometry and immutable Track C sources."""

import copy
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import pytest
from penforge_contracts import GlyphOutline, StrokeDocument, content_hash

from packages.extraction.geometry import (
    CaptureToFont,
    outline_svg,
    rasterize_outline,
    trace_mask,
    vectorize_strokes,
)


def raster(outline, shape=(24, 24), baseline=18, scale=10):
    return rasterize_outline(outline, shape, baseline_px=baseline, units_per_px=scale)


@pytest.mark.parametrize("shape", ["point", "thin_line", "diagonal", "holes", "descender"])
def test_exact_pixel_union_round_trip_preserves_tiny_ink_holes_and_multiple_contours(shape):
    mask = np.zeros((24, 24), dtype=np.uint8)
    if shape == "point":
        mask[5, 7] = 255
    elif shape == "thin_line":
        mask[3:18, 7] = 255
        mask[17, 7:15] = 255
    elif shape == "diagonal":
        mask[5, 5] = mask[6, 6] = mask[7, 7] = 255
    elif shape == "holes":
        mask[2:22, 2:22] = 255
        mask[5:19, 5:19] = 0
        mask[9:14, 9:14] = 255
    else:
        mask[5:23, 7:12] = 255
    outline = trace_mask(mask, [103], baseline_px=18, units_per_px=10)
    assert np.array_equal(mask, raster(outline))
    assert all(c.closed and c.segments[-1].to == c.start for c in outline.contours)
    if shape == "diagonal":
        assert len(outline.contours) == 3
    if shape == "holes":
        assert len(outline.contours) == 3
        areas = []
        for contour in outline.contours:
            points = [contour.start, *[segment.to for segment in contour.segments]]
            areas.append(
                sum(a.x * b.y - b.x * a.y for a, b in zip(points[:-1], points[1:], strict=True)) / 2
            )
        assert sum(a < 0 for a in areas) == 2 and sum(a > 0 for a in areas) == 1
    if shape == "descender":
        assert min(p.to.y for c in outline.contours for p in c.segments) < 0


def test_seeded_pixel_topologies_round_trip_without_expanding_noise_specks():
    rng = np.random.default_rng(404)
    for _ in range(30):
        mask = (rng.random((12, 12)) > 0.6).astype(np.uint8) * 255
        outline = trace_mask(mask, [97], baseline_px=9, units_per_px=10)
        assert np.array_equal(mask, raster(outline, (12, 12), 9, 10))


def test_svg_bounds_include_control_points_and_closed_endpoint_curves_are_not_discarded():
    outline = GlyphOutline.model_validate(
        {
            "schema_version": 1,
            "units_per_em": 1000,
            "coordinate_system": {"origin": "baseline", "x_positive": "right", "y_positive": "up"},
            "codepoints": [97],
            "metrics": {"advance_width": 300.0, "left_side_bearing": 0.0},
            "contours": [
                {
                    "closed": True,
                    "start": {"x": 100.0, "y": 0.0},
                    "segments": [
                        {
                            "type": "cubic",
                            "c1": {"x": 900.0, "y": 1000.0},
                            "c2": {"x": -300.0, "y": 1000.0},
                            "to": {"x": 100.0, "y": 0.0},
                        },
                    ],
                }
            ],
        }
    )
    svg = ElementTree.fromstring(outline_svg(outline))
    x, y, w, h = map(float, svg.attrib["viewBox"].split())
    assert x <= -300 and x + w >= 900 and y <= -1000
    rendered = rasterize_outline(
        outline, (130, 140), baseline_px=110, units_per_px=10, origin_x_px=40
    )
    assert np.count_nonzero(rendered) > 100


def document(pointer="mouse", pressure=False):
    points = [{"x": 0.25, "y": 0.3, "t_ms": 0.0}, {"x": 0.5, "y": 0.8, "t_ms": 20.0}]
    if pressure:
        for point in points:
            point.update(pressure=0.4, tilt_x=15.0, tilt_y=-5.0, twist=20.0)
    return StrokeDocument.model_validate(
        {
            "schema_version": 1,
            "coordinate_system": {
                "origin": "top_left",
                "x_positive": "right",
                "y_positive": "down",
                "normalized": True,
            },
            "capture_size_px": {"width": 120, "height": 100},
            "timing": "observed",
            "strokes": [{"stroke_id": "s1", "pointer_type": pointer, "points": points}],
            "operations": [],
        }
    )


@pytest.mark.parametrize("pointer,pressure", [("mouse", False), ("keyboard", False), ("pen", True)])
def test_digital_capture_uses_supplied_uniform_frame_and_preserves_observed_metadata(
    pointer, pressure
):
    source = document(pointer, pressure)
    before = source.model_dump(mode="json")
    digest = content_hash(before)
    outline, provenance = vectorize_strokes(
        source, [103], CaptureToFont(baseline_y=0.75, units_per_px=2.0, pen_width_px=3.0)
    )
    assert source.model_dump(mode="json") == before and provenance["source_document_hash"] == digest
    assert provenance["image_segmentation"] is False and provenance["ocr"] is False
    assert provenance["pen_width_source"] == "explicit_brush_configuration"
    assert provenance["pressure_policy"] == "observed_only"
    assert outline.metrics.advance_width == 240
    points = [s.to for c in outline.contours for s in c.segments]
    assert min(p.y for p in points) < 0
    assert min(p.x for p in points) < 60 and max(p.x for p in points) > 120
    if not pressure:
        assert all(p.pressure is None for s in source.strokes for p in s.points)


def test_digital_derived_operations_are_reversible_without_rewriting_parent():
    source = document()
    before = source.model_dump(mode="json")
    changed = copy.deepcopy(before)
    changed["parent_source_hash"] = content_hash(before)
    changed["operations"] = [
        {"type": "translate", "stroke_ids": ["s1"], "dx": 0.1, "dy": -0.1},
        {"type": "translate", "stroke_ids": ["s1"], "dx": -0.1, "dy": 0.1},
    ]
    original, _ = vectorize_strokes(source, [97], CaptureToFont())
    restored, _ = vectorize_strokes(StrokeDocument.model_validate(changed), [97], CaptureToFont())
    assert restored.model_dump(mode="json") == original.model_dump(mode="json")
    assert source.model_dump(mode="json") == before


def test_legacy_absent_dimensions_fail_without_fabricated_capture_metadata():
    root = Path(__file__).resolve().parents[2]
    source = StrokeDocument.model_validate_json(
        (root / "packages/contracts/fixtures/legacy-strokes.v1.json").read_text()
    )
    before = source.model_dump(mode="json")
    with pytest.raises(ValueError, match="capture_dimensions_required"):
        vectorize_strokes(source, [97], CaptureToFont())
    assert source.model_dump(mode="json") == before


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1.0])
def test_invalid_capture_scale_cannot_become_an_outline(value):
    with pytest.raises(ValueError):
        CaptureToFont(units_per_px=value)


def test_zero_area_line_contour_cannot_be_reviewed_as_a_valid_filled_glyph():
    with pytest.raises(ValueError, match="contour_has_no_filled_area"):
        GlyphOutline.model_validate(
            {
                "schema_version": 1,
                "units_per_em": 1000,
                "coordinate_system": {
                    "origin": "baseline",
                    "x_positive": "right",
                    "y_positive": "up",
                },
                "codepoints": [97],
                "metrics": {"advance_width": 100.0, "left_side_bearing": 0.0},
                "contours": [
                    {
                        "closed": True,
                        "start": {"x": 0.0, "y": 0.0},
                        "segments": [
                            {"type": "line", "to": {"x": 10.0, "y": 10.0}},
                            {"type": "line", "to": {"x": 0.0, "y": 0.0}},
                        ],
                    }
                ],
            }
        )


def test_digital_edge_clipping_is_reported_without_rewriting_source():
    source = document()
    value = source.model_dump(mode="json")
    value["strokes"][0]["points"][0]["x"] = 0.0
    source = StrokeDocument.model_validate(value)
    before = source.model_dump(mode="json")
    _, provenance = vectorize_strokes(source, [103], CaptureToFont())
    assert provenance["flags"] == ["border_touch"]
    assert source.model_dump(mode="json") == before
