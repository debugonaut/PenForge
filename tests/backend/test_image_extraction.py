"""Track A preserves cell evidence; known labels are never recognition truth."""

import hashlib
import io
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

from packages.extraction import extract_image, load_template, template_hash, template_png
from packages.extraction.fixtures import perturb_sheet, synthetic_sheet
from scripts.evaluate_extraction import validate_fixture_configuration


@pytest.mark.parametrize("page", [0, 1])
@pytest.mark.parametrize("lighting", [0.0, 0.35, 0.6])
def test_blank_guided_page_does_not_turn_printed_guides_or_lighting_into_glyphs(page, lighting):
    manifest = load_template(page)
    original = template_png(manifest)
    distorted = perturb_sheet(original, {"lighting_gradient": lighting})
    before = hashlib.sha256(distorted).hexdigest()
    result = extract_image(distorted, manifest, template_hash(manifest))
    assert not result.reasons
    assert not result.glyphs
    assert result.quality["empty_cell_count"] == len(manifest.cells)
    assert result.quality["expected_text_is_ground_truth"] is False
    assert result.quality["recognition"] == "unavailable"
    assert hashlib.sha256(distorted).hexdigest() == before


@pytest.mark.parametrize(
    "condition", [{}, {"perspective_fraction": 0.04}, {"rotation_degrees": 3.0}]
)
def test_structured_sheet_preserves_authoritative_destinations_and_unknown_identity(condition):
    manifest = load_template(0)
    original, ground = synthetic_sheet(manifest, missing_codepoints=(33, 65))
    source = perturb_sheet(original, condition)
    result = extract_image(source, manifest, template_hash(manifest))
    assert not result.reasons
    expected = {
        cell.cell_id: cell.codepoints for cell in manifest.cells if np.any(ground[cell.cell_id])
    }
    assert {glyph.cell_id: glyph.codepoints for glyph in result.glyphs} == expected
    for glyph in result.glyphs:
        assert "identity_unverified" in glyph.flags
        assert glyph.crop_png.startswith(b"\x89PNG")
        assert glyph.mask_png.startswith(b"\x89PNG")
        assert glyph.outline.codepoints == glyph.codepoints
    assert all(cell["recognition_score"] is None for cell in result.quality["cells"])
    assert result.quality["source_bytes_hash"] == "sha256:" + hashlib.sha256(source).hexdigest()


def test_dark_ink_crossing_printed_guides_survives_and_border_touch_is_not_erased():
    manifest = load_template(0)
    cell = manifest.cells[0]
    image = Image.open(io.BytesIO(template_png(manifest))).convert("RGB")
    rect = cell.rect_mm
    x, y = round(rect.x * 8), round(rect.y * 8)
    ImageDraw.Draw(image).line(
        (x + 40, y, x + 40, y + round(rect.height * 8) - 1), fill="black", width=5
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    result = extract_image(output.getvalue(), manifest, template_hash(manifest))
    glyph = next(g for g in result.glyphs if g.cell_id == cell.cell_id)
    assert "border_touch" in glyph.flags
    mask = cv2.imdecode(np.frombuffer(glyph.mask_png, np.uint8), 0)
    for guide_y in (5, 11, 19, 24):
        assert mask[round(guide_y * 8), 40] == 255


def test_missing_page_identity_is_an_actionable_retake_not_an_ocr_guess():
    manifest = load_template(0)
    image = Image.open(io.BytesIO(template_png(manifest))).convert("RGB")
    r = manifest.page_identity.rect_mm
    ImageDraw.Draw(image).rectangle(
        (r.x * 8, r.y * 8, (r.x + r.width) * 8, (r.y + r.height) * 8), fill="white"
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    result = extract_image(output.getvalue(), manifest, template_hash(manifest))
    assert result.reasons == ("page_identity_unreadable",)
    assert result.quality["status"] == "needs_better_input"
    assert not result.glyphs


def test_page_manifest_hash_and_marker_identity_mismatches_are_rejected():
    first, second = load_template(0), load_template(1)
    data = template_png(first)
    with pytest.raises(ValueError, match="template_manifest_hash_mismatch"):
        extract_image(data, first, template_hash(second))
    result = extract_image(data, second, template_hash(second))
    assert result.reasons == ("template_marker_mismatch",)
    assert not result.glyphs


def test_swapped_printed_markers_cannot_silently_warp_cells_into_wrong_positions():
    manifest = load_template(0)
    image = Image.open(io.BytesIO(template_png(manifest))).convert("RGB")
    boxes = [
        (
            round(m.rect_mm.x * 8),
            round(m.rect_mm.y * 8),
            round((m.rect_mm.x + m.rect_mm.width) * 8),
            round((m.rect_mm.y + m.rect_mm.height) * 8),
        )
        for m in manifest.markers.items[:2]
    ]
    left, right = image.crop(boxes[0]), image.crop(boxes[1])
    image.paste(right, boxes[0][:2])
    image.paste(left, boxes[1][:2])
    output = io.BytesIO()
    image.save(output, format="PNG")
    result = extract_image(output.getvalue(), manifest, template_hash(manifest))
    assert result.reasons == ("marker_order_mismatch",)
    assert not result.glyphs


@pytest.mark.parametrize(
    "condition,reason",
    [({"scale": 0.25}, "low_resolution"), ({"blur_sigma_px": 5.0}, "too_blurry")],
)
def test_measured_validation_policy_requests_actionable_retakes(condition, reason):
    root = Path(__file__).resolve().parents[2]
    policy = json.loads((root / "packages/contracts/policies/structured-latin.v1.json").read_text())
    gates = {
        key: policy[key]
        for key in ("evidence_hash", "minimum_pixels_per_mm", "minimum_laplacian_variance")
    }
    manifest = load_template(0)
    source, _ = synthetic_sheet(manifest)
    result = extract_image(
        perturb_sheet(source, condition),
        manifest,
        template_hash(manifest),
        {"quality_gates": gates},
    )
    assert result.reasons == (reason,)
    assert result.quality["numeric_quality_gate_source"] == policy["evidence_hash"]
    assert not result.glyphs


@pytest.mark.parametrize("mutation", ["same_family", "alias", "font_hash", "license_hash"])
def test_synthetic_source_and_family_cannot_leak_across_splits_or_change_silently(mutation):
    root = Path(__file__).resolve().parents[2]
    config = json.loads((root / "research/evaluation/fixtures/phase04/config.v3.json").read_text())
    if mutation == "same_family":
        config["splits"]["test"] = ["caveat-all-weights-global"]
    elif mutation == "alias":
        config["splits"]["train"] = ["caveat-light-alias"]
    else:
        config[mutation] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        validate_fixture_configuration(config)


def test_recorded_synthetic_baseline_meets_its_measured_regression_gates():
    root = Path(__file__).resolve().parents[2]
    folder = root / "tests/backend/fixtures"
    report_bytes = (folder / "extraction-baseline.v5.json").read_bytes()
    report = json.loads(report_bytes)
    gates = json.loads((folder / "extraction-gates.v5.json").read_text())
    assert gates["baseline_hash"] == "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    assert gates["selected_method"] == "exact_polyline"
    for record in report["records"]:
        if record["condition"] in {"low_resolution", "severe_blur"}:
            expected = "low_resolution" if record["condition"] == "low_resolution" else "too_blurry"
            assert record["retake_reasons"] == [expected] and record["returned"] == 0
        elif record["method"] == gates["selected_method"]:
            assert record["hit_rate"] >= gates["minimum_cell_hit_rate"]
            assert record["false_glyph_count"] <= gates["maximum_false_glyph_count"]
            assert record["cell_mask_iou_mean"] >= gates["minimum_mean_cell_iou"]
            assert record["vector_raster_iou_mean"] >= gates["minimum_mean_vector_iou"]
            assert record["boundary_f1_1px_mean"] >= gates["minimum_boundary_f1_1px"]
            assert record["identity_reviews_required"] == record["returned"]
    assert report["recognition_accuracy"] is None
    assert report["production_validated"] is False
