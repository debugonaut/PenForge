"""Analytic fixed-brush fidelity and immutable legacy/capture regressions."""

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from penforge_contracts import (
    AnalyzeSamplesJob,
    AnalyzeSamplesJobV2,
    StrokeDocument,
    content_hash,
    parse_analysis_payload,
)
from penforge_contracts.canvas import drawing_provenance

from packages.extraction.geometry import CaptureToFont, rasterize_outline, vectorize_strokes
from packages.extraction.round_strokes import vectorize_round_strokes

ROOT = Path(__file__).resolve().parents[2]


def capture(strokes):
    return StrokeDocument.model_validate(
        {
            "schema_version": 1,
            "coordinate_system": {
                "origin": "top_left",
                "x_positive": "right",
                "y_positive": "down",
                "normalized": True,
            },
            "capture_size_px": {"width": 100, "height": 100},
            "timing": "observed",
            "strokes": [
                {
                    "stroke_id": f"s{i}",
                    "pointer_type": "mouse",
                    "points": [
                        {"x": x / 100, "y": y / 100, "t_ms": i * 1000 + j}
                        for j, (x, y) in enumerate(points)
                    ],
                }
                for i, points in enumerate(strokes)
            ],
            "operations": [],
        }
    )


@pytest.mark.parametrize(
    "strokes,contours",
    [
        ([[(25, 30), (75, 70)]], 1),
        ([[(50, 50)]], 1),
        ([[(50, 50), (50, 50)]], 1),
        ([[(20, 50), (80, 50)], [(50, 20), (50, 80)]], 1),
        ([[(25, 25), (75, 25), (75, 75), (25, 75), (25, 25)]], 2),
        ([[(20, 30), (80, 30)], [(50, 60)]], 2),
    ],
)
def test_round_brush_matches_analytic_distance_preserving_dots_counters_and_overlaps(
    strokes, contours
):
    # Independent reference: a round polyline is the union of radius-r capsules.
    doc = capture(strokes)
    outline, proof = vectorize_round_strokes(
        doc, [103], CaptureToFont(units_per_px=1, pen_width_px=6)
    )
    assert len(outline.contours) == contours
    assert any(s.type == "quadratic" for c in outline.contours for s in c.segments)
    assert all(c.segments[-1].to == c.start for c in outline.contours)
    image = rasterize_outline(outline, (100, 100), baseline_px=75, units_per_px=1) > 0
    y, x = np.mgrid[:100, :100]
    grid = np.stack((x, y), axis=-1)
    distance = np.full((100, 100), np.inf)
    for points in strokes:
        pairs = list(zip(points, points[1:])) or [(points[0], points[0])]
        for a, b in pairs:
            a, b = np.array(a), np.array(b)
            delta = b - a
            t = np.clip(np.sum((grid - a) * delta, axis=-1) / max(np.sum(delta**2), 1), 0, 1)
            distance = np.minimum(
                distance, np.linalg.norm(grid - a - t[..., None] * delta, axis=-1)
            )
    # Raster fill includes boundary pixels; compare only points >1.5 px from the edge.
    away_from_boundary = abs(distance - 3) > 1.5
    assert np.array_equal(image[away_from_boundary], (distance <= 3)[away_from_boundary])
    assert proof["source_unchanged"] and proof["raster_supersampling"] is None
    assert proof["output_segment_count"] < 100


def test_pressure_is_retained_but_does_not_change_fixed_brush_source_or_outline():
    doc = capture([[(25, 30), (50, 85)]])
    original = doc.model_dump(mode="json")
    pen = copy.deepcopy(original)
    pen["strokes"][0]["pointer_type"] = "pen"
    for i, point in enumerate(pen["strokes"][0]["points"]):
        point.update(pressure=0.1 + 0.8 * i, tilt_x=15, tilt_y=-5, twist=20)
    outline, _ = vectorize_round_strokes(doc, [103], CaptureToFont())
    pen_doc = StrokeDocument.model_validate(pen)
    second, provenance = vectorize_round_strokes(pen_doc, [103], CaptureToFont())
    assert second == outline
    assert doc.model_dump(mode="json") == original and pen_doc.model_dump(mode="json") == pen
    assert provenance["pressure_policy"] == "preserved_not_applied"
    assert min(s.to.y for c in outline.contours for s in c.segments) < 0
    assert outline.metrics.advance_width == 200
    assert provenance["source_document_hash"] == content_hash(pen)
    assert vectorize_round_strokes(pen_doc, [103], CaptureToFont()) == (second, provenance)


def test_clipped_ink_is_flagged_and_reversible_operations_preserve_source():
    doc = capture([[(0, 50), (50, 50)]])
    original = doc.model_dump(mode="json")
    outline, proof = vectorize_round_strokes(doc, [102], CaptureToFont())
    assert proof["flags"] == ["border_touch"]
    assert min(s.to.x for c in outline.contours for s in c.segments) >= 0
    derived = copy.deepcopy(original)
    derived.update(
        parent_source_hash=content_hash(original),
        operations=[
            {"type": "translate", "stroke_ids": ["s0"], "dx": 0.1, "dy": 0},
            {"type": "translate", "stroke_ids": ["s0"], "dx": -0.1, "dy": 0},
        ],
    )
    assert vectorize_round_strokes(derived, [102], CaptureToFont())[0] == outline
    assert doc.model_dump(mode="json") == original
    derived["operations"][0]["dx"] = -0.1
    with pytest.raises(ValueError, match="outside_capture"):
        vectorize_round_strokes(derived, [102], CaptureToFont())


def test_geometry_budget_toolchain_and_space_fail_closed(monkeypatch):
    import packages.extraction.round_strokes as geometry

    doc = capture([[(20, 20), (80, 80)]])
    with pytest.raises(ValueError, match="space_requires_metrics_only"):
        vectorize_round_strokes(doc, [32], CaptureToFont())
    policy = geometry.canvas_policy()
    monkeypatch.setattr(geometry, "canvas_policy", lambda: policy | {"max_source_points": 1})
    with pytest.raises(ValueError, match="stroke_geometry_budget_exceeded"):
        vectorize_round_strokes(doc, [65], CaptureToFont())
    monkeypatch.setattr(geometry, "canvas_policy", lambda: policy | {"max_output_segments": 1})
    with pytest.raises(ValueError, match="stroke_geometry_budget_exceeded"):
        vectorize_round_strokes(doc, [65], CaptureToFont())
    monkeypatch.setattr(
        geometry, "canvas_policy", lambda: policy | {"pathops_version": "unsupported"}
    )
    with pytest.raises(ValueError, match="drawing_toolchain_mismatch"):
        vectorize_round_strokes(doc, [65], CaptureToFont())


def test_legacy_public_fixture_stays_byte_identical():
    doc = StrokeDocument.model_validate_json(
        (ROOT / "packages/contracts/fixtures/strokes.v1.json").read_bytes()
    )
    outline, proof = vectorize_strokes(doc, [103], CaptureToFont())
    assert (
        proof["source_document_hash"]
        == "sha256:f79331314368efb8e6af679791dcbeb11450c2dbd404fd3d6417a7eb48276d7e"
    )
    assert (
        content_hash(outline.model_dump(mode="json"))
        == "sha256:290b56fbc61415b1b5914872ce64ae7053786563cca01097360c4052137669e3"
    )
    assert all(proof[k] == v for k, v in drawing_provenance().items())


def test_drawing_v2_is_explicit_and_v1_is_not_reinterpreted():
    legacy = json.loads(
        (ROOT / "packages/contracts/fixtures/analysis-drawing.v1.json").read_bytes()
    )
    assert type(parse_analysis_payload(legacy)) is AnalyzeSamplesJob
    modern = json.loads(
        (ROOT / "packages/contracts/fixtures/analysis-drawing.v2.json").read_bytes()
    )
    assert modern == legacy | {"schema_version": 2, "drawing_mode": "fixed_round_v1"}
    assert type(parse_analysis_payload(modern)) is AnalyzeSamplesJobV2
    assert parse_analysis_payload(modern).model_dump(mode="json") == modern
    for bad in [
        legacy | {"drawing_mode": "fixed_round_v1"},
        modern | {"script_code": "Deva"},
        modern | {"assignments": []},
        modern | {"schema_version": True},
        modern | {"schema_version": 3},
        modern | {"drawing_mode": "unknown"},
        modern | {"sample_ids": [*modern["sample_ids"], "00000000-0000-4000-8000-000000000003"]},
    ]:
        with pytest.raises(ValueError):
            parse_analysis_payload(bad)


def test_legacy_request_digest_body_does_not_gain_an_optional_null_field():
    from uuid import UUID

    from app.modules.analysis.schemas import AnalysisCreate

    legacy = json.loads(
        (ROOT / "packages/contracts/fixtures/analysis-drawing.v1.json").read_bytes()
    )
    body = AnalysisCreate.model_validate(
        {k: legacy[k] for k in ("job_type", "sample_ids", "assignments", "options")}
    )
    assert "drawing_mode" not in body.model_dump(mode="json")
    assert body.payload(UUID(legacy["project_id"]), None).model_dump(mode="json") == legacy
