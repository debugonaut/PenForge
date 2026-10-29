"""User-bound crops retain actual ink and never manufacture recognized identities."""

import copy
import hashlib
import io
from uuid import uuid4

import numpy as np
import pytest
from penforge_contracts import GlyphOutline, content_hash
from PIL import Image, ImageDraw, ImageOps

from packages.extraction import manual_regions as manual
from packages.extraction.isolated import analyze_isolated


def encoded(image, *, format="PNG", **options):
    output = io.BytesIO()
    image.save(output, format=format, **options)
    return output.getvalue()


def fixture():
    image = Image.new("RGB", (160, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 50, 60), fill="black")
    draw.rectangle((28, 28, 42, 52), fill="white")
    draw.rectangle((90, 30, 105, 85), fill="black")
    return encoded(image)


def region(data, *, rect=None, **changes):
    value = {
        "region_id": str(uuid4()),
        "sample_id": "d90e70a8-b2a1-4a13-81a1-fb0d993a40f0",
        "source_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "source_width": 160,
        "source_height": 120,
        "rect": rect or {"x": 10, "y": 10, "width": 60, "height": 80},
        "codepoints": [65],
        "baseline_y_px": 70.0,
        "units_per_px": 10.0,
        "ink_threshold": 180,
    }
    return value | changes


def extract(data, assignments):
    return manual.extract_manual_regions(
        data, assignments, "sha256:" + hashlib.sha256(data).hexdigest()
    )


def test_batch_preserves_holes_descenders_source_and_explicit_assignments(monkeypatch):
    data = fixture()
    rows = [
        region(data),
        region(data, rect={"x": 80, "y": 10, "width": 50, "height": 90}, codepoints=[103]),
    ]
    before = copy.deepcopy(rows)
    opens = []
    real_open = Image.open

    def record_open(*args, **kwargs):
        opens.append(1)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(manual.Image, "open", record_open)
    result = extract(data, rows)
    assert len(opens) == 1
    assert not result.reasons
    assert rows == before
    assert len(result.glyphs) == 2
    assert len(result.glyphs[0].outline.contours) == 2
    second = result.glyphs[1].outline
    assert any(segment.to.y < 0 for c in second.contours for segment in c.segments)
    assert [g.outline.metrics.advance_width for g in result.glyphs] == [600, 500]
    assert result.quality["source_bytes_hash"] == rows[0]["source_sha256"]
    assert result.quality["source_decode_count"] == 1
    assert result.quality["source_unchanged"] is True
    assert result.quality["ocr"] is False
    assert result.quality["recognition"] == "unavailable"
    assert result.quality["identity_source"] == "user_assignment"
    assert result.quality["expected_text_is_ground_truth"] is False
    assert result.quality["candidate_count"] == 2
    assert result.quality["duration_ms"] >= result.quality["decode_duration_ms"] >= 0
    for glyph, row in zip(result.glyphs, rows, strict=True):
        assert glyph.flags == ("identity_unverified",)
        assert glyph.region_id == row["region_id"]
        assert glyph.provenance["assignment"] == row
        assert glyph.provenance["assignment_hash"] == content_hash(row)
        assert glyph.provenance["source_unchanged"] is True
        assert glyph.provenance["outline_hash"] == content_hash(
            glyph.outline.model_dump(mode="json")
        )
        assert glyph.crop_png.startswith(b"\x89PNG")
        assert glyph.mask_png.startswith(b"\x89PNG")


def test_white_alpha_compositing_and_strict_integer_luma_match_preview_policy():
    image = Image.new("RGBA", (160, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 35, 55), fill=(0, 0, 0, 100))
    draw.rectangle((40, 20, 55, 55), fill=(180, 180, 180, 255))
    data = encoded(image)
    row = region(data)
    result = extract(data, [row])
    assert len(result.glyphs) == 1
    rgba = np.asarray(image.crop((10, 10, 70, 90)), dtype=np.uint32)
    alpha = rgba[:, :, 3:4]
    expected_rgb = ((rgba[:, :, :3] * alpha + 255 * (255 - alpha) + 127) // 255).astype(np.uint8)
    rgb = expected_rgb.astype(np.uint32)
    gray = (299 * rgb[:, :, 0] + 587 * rgb[:, :, 1] + 114 * rgb[:, :, 2] + 500) // 1000
    expected_mask = np.where(gray < 180, 255, 0).astype(np.uint8)
    glyph = result.glyphs[0]
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(glyph.crop_png))), expected_rgb)
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(glyph.mask_png))), expected_mask)
    assert not expected_mask[15, 35]  # Exactly 180 is excluded by strict less-than.
    assert expected_mask[15, 15] == 255  # Alpha-composited black is gray 155.
    assert not expected_mask[0].any()  # Transparent black remains white background.


def test_exif_orientation_is_applied_before_source_dimensions_and_region_coordinates():
    image = Image.new("RGB", (120, 160), "white")
    ImageDraw.Draw(image).rectangle((20, 30, 45, 60), fill="black")
    exif = Image.Exif()
    exif[274] = 6
    data = encoded(image, format="JPEG", exif=exif, quality=100)
    row = region(data, rect={"x": 80, "y": 10, "width": 60, "height": 60})
    result = extract(data, [row])
    assert len(result.glyphs) == 1
    oriented = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    crop = Image.open(io.BytesIO(result.glyphs[0].crop_png))
    assert np.array_equal(np.asarray(crop), np.asarray(oriented.crop((80, 10, 140, 70))))
    assert result.quality["coordinate_frame"] == "exif_transposed_source_pixels"
    assert (result.quality["source_width"], result.quality["source_height"]) == (160, 120)
    with pytest.raises(ValueError, match="dimensions_mismatch"):
        extract(
            data,
            [
                row
                | {
                    "source_width": 120,
                    "source_height": 160,
                    "rect": {"x": 10, "y": 10, "width": 60, "height": 60},
                }
            ],
        )


@pytest.mark.parametrize("kind", ["blank", "clipped", "guide"])
def test_empty_or_ambiguous_edge_ink_never_becomes_a_candidate(kind):
    image = Image.new("RGB", (160, 120), "white")
    draw = ImageDraw.Draw(image)
    if kind == "clipped":
        draw.rectangle((10, 30, 30, 50), fill="black")
    elif kind == "guide":
        draw.line((0, 50, 159, 50), fill="black", width=2)
    data = encoded(image)
    result = extract(data, [region(data)])
    assert not result.glyphs
    expected = "empty_ink" if kind == "blank" else "crop_touches_ink"
    assert result.reasons == (expected,)
    assert result.quality["status"] == "needs_better_input"
    assert result.quality["regions"][0]["reasons"] == [expected]


def test_one_bad_region_preserves_valid_pending_candidates_and_explicit_partial_result():
    data = fixture()
    rows = [region(data), region(data, rect={"x": 125, "y": 10, "width": 30, "height": 60})]
    result = extract(data, rows)
    assert len(result.glyphs) == 1
    assert result.glyphs[0].region_id == rows[0]["region_id"]
    assert result.reasons == ("empty_ink",)
    assert result.quality["status"] == "needs_better_input"


def test_adjustable_threshold_can_exclude_pale_paper_rules_without_erasing_crossing_dark_ink():
    image = Image.new("RGB", (160, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.line((0, 40, 159, 40), fill=(160, 160, 160), width=2)
    draw.rectangle((20, 20, 50, 60), outline=(20, 40, 110), width=8)
    data = encoded(image)
    row = region(data)
    default = extract(data, [row])
    assert default.reasons == ("crop_touches_ink",)
    assert not default.glyphs
    assert "possible_guide_or_clipped_ink" in default.quality["regions"][0]["flags"]
    adjusted = extract(data, [row | {"ink_threshold": 120}])
    assert not adjusted.reasons and len(adjusted.glyphs) == 1
    glyph = adjusted.glyphs[0]
    mask = np.asarray(Image.open(io.BytesIO(glyph.mask_png)))
    assert mask[30, 14] == 255  # The actual dark pen crossing the rule survives.
    assert mask[30, 0] == 0  # The lighter guide at the crop edge is excluded.
    assert glyph.flags == ("identity_unverified",)
    assert glyph.provenance["assignment"]["ink_threshold"] == 120
    assert glyph.provenance["source_bytes_hash"] == row["source_sha256"]


@pytest.mark.parametrize(
    "mutation", ["hash", "dimensions", "bounds", "label", "scale", "id", "extra"]
)
def test_forged_or_invalid_regions_fail_before_candidate_creation(mutation):
    data = fixture()
    row = region(data)
    rows = [row]
    if mutation == "hash":
        row["source_sha256"] = "sha256:" + "0" * 64
    elif mutation == "dimensions":
        row["source_width"] = 159
    elif mutation == "bounds":
        row["rect"]["x"] = 150
    elif mutation == "label":
        row["codepoints"] = [32]
    elif mutation == "scale":
        rows.append(region(data, units_per_px=11.0))
    elif mutation == "id":
        rows.append(copy.deepcopy(row))
    else:
        row["trusted_identity"] = True
    with pytest.raises(ValueError):
        extract(data, rows)


def test_source_substitution_is_rejected_before_decoding(monkeypatch):
    data = fixture()
    row = region(data)
    monkeypatch.setattr(manual.Image, "open", lambda *a, **k: pytest.fail("decoder called"))
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        manual.extract_manual_regions(data + b"changed", [row], row["source_sha256"])


def test_count_and_aggregate_crop_budgets_apply_before_image_decode(monkeypatch):
    data = fixture()
    monkeypatch.setattr(manual.Image, "open", lambda *a, **k: pytest.fail("decoder called"))
    with pytest.raises(ValueError, match="region_budget"):
        extract(data, [region(data) for _ in range(97)])
    rows = [
        region(
            data,
            source_width=4000,
            source_height=3000,
            rect={"x": 0, "y": 0, "width": 2000, "height": 1500},
        )
        for _ in range(3)
    ]
    with pytest.raises(ValueError, match="batch_budget"):
        extract(data, rows)


def test_actual_isolated_child_returns_source_bound_manual_region_artifacts(tmp_path):
    data = fixture()
    row = region(data)
    (tmp_path / "source").write_bytes(data)
    report = analyze_isolated(
        tmp_path,
        {
            "source_type": "freeform_photo",
            "source_hash": row["source_sha256"],
            "assignment": None,
            "image_mode": "manual_regions_v1",
            "image_regions": [row],
        },
        cancel_check=lambda: None,
    )
    assert report["state"] == "analyzed"
    assert report["isolation"] in {"macos_seatbelt_network_deny", "linux_seccomp_network_deny"}
    assert report["quality"]["source_decode_count"] == 1
    assert report["resources"]["wall_seconds"] > 0
    item = report["candidates"][0]
    assert item["region_id"] == row["region_id"] and "cell_id" not in item
    assert item["flags"] == ["identity_unverified"]
    assert item["provenance"]["assignment"] == row
    files = {file["name"]: file for file in report["files"]}
    assert len(files) == 3
    for name, file in files.items():
        blob = (tmp_path / name).read_bytes()
        assert len(blob) == file["size_bytes"]
        assert "sha256:" + hashlib.sha256(blob).hexdigest() == file["sha256"]
    outline = GlyphOutline.model_validate_json((tmp_path / item["outline"]).read_bytes())
    assert item["provenance"]["outline_hash"] == content_hash(outline.model_dump(mode="json"))
