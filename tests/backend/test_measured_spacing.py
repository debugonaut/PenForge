"""Measured spacing: bounding, exactness, fallback and determinism.

Geometry here is authored synthetic control data. These tests establish font
engineering behaviour; they are not handwriting-quality or legibility evidence.
"""

import hashlib
import io
import json
import os
import subprocess
import sys

import pytest
from font_fixtures import fixture_glyphs, fixture_manifest
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from penforge_font_engine.compiler import compile_font, coverage, sfnt_bytes
from penforge_font_engine.spacing import RUNS, measure, shape_run, verify_shifts
from penforge_font_engine.validation import check_shaping, validate_font

from scripts.deva_fixture import fixture as deva_fixture


def varied():
    """Deterministic per-glyph variation: realistic spread without overlap."""
    rows = fixture_glyphs()
    for row in rows:
        cp = row["outline"]["codepoints"][0]
        if cp == 32:
            continue
        shift = (cp % 7 - 3) * 2
        for contour in row["outline"]["contours"]:
            contour["start"]["x"] += shift
            for segment in contour["segments"]:
                for key in ("to", "c", "c1", "c2"):
                    if key in segment:
                        segment[key]["x"] += shift
        row["outline"]["metrics"]["advance_width"] += (cp % 5) * 8
    return rows


def advances(data, text, *, kern):
    return shape_run(data, text, kern=kern)[1]


def test_disabled_emits_no_gpos_and_keeps_static_advances():
    rows = varied()
    manifest = fixture_manifest()
    data = compile_font(manifest, rows, "ttf")
    font = TTFont(io.BytesIO(data))
    assert "GPOS" not in font
    assert (
        measure(
            {r["outline"]["codepoints"][0]: (r["outline"], "user_capture") for r in rows}, manifest
        )["status"]
        == "disabled"
    )
    report = coverage(rows, manifest)
    assert report["spacing"]["status"] == "disabled"
    assert "kern_unsupported_in_some_apps" not in report["warnings"]
    for text in RUNS:
        assert advances(data, text, kern=True) == advances(data, text, kern=False)


def test_enabled_kerning_is_bounded_exact_and_reduces_population_spread():
    rows = varied()
    manifest = fixture_manifest(options={"spacing": 100})
    data = compile_font(manifest, rows, "ttf")
    font = TTFont(io.BytesIO(data))
    assert {r.FeatureTag for r in font["GPOS"].table.FeatureList.FeatureRecord} == {"kern"}
    report = check_shaping(data, rows, manifest)["measured_spacing"]
    assert report["status"] == "applied"
    assert 0 < report["corrected_pairs"] <= 4096
    assert report["max_abs_shift"] <= report["amplitude_units"] == 20
    population = report["population"]
    assert population["after"]["stdev"] < population["before"]["stdev"]
    assert population["after"]["samples"] == population["before"]["samples"]
    assert (
        verify_shifts(
            io.BytesIO(data).getvalue(),
            RUNS,
            measure(
                {r["outline"]["codepoints"][0]: (r["outline"], "user_capture") for r in rows},
                manifest,
            )["pairs"],
        )
        == []
    )
    assert coverage(rows, manifest)["spacing"]["corrected_pairs"] == report["corrected_pairs"]


def test_control_scales_amplitude_without_changing_policy():
    rows = varied()
    half_manifest = fixture_manifest(options={"spacing": 50})
    full_manifest = fixture_manifest(options={"spacing": 100})
    half = compile_font(half_manifest, rows, "ttf")
    full = compile_font(full_manifest, rows, "ttf")
    assert half != full
    half_report = check_shaping(half, rows, half_manifest)["measured_spacing"]
    full_report = check_shaping(full, rows, full_manifest)["measured_spacing"]
    assert half_report["amplitude_units"] == 10
    assert full_report["amplitude_units"] == 20
    assert 0 < half_report["max_abs_shift"] <= 10
    assert half_report["max_abs_shift"] < full_report["max_abs_shift"]
    assert half_report["policy"] == full_report["policy"] == "penforge-spacing-v2"


def test_feature_off_fallback_keeps_base_metrics_and_positive_advances():
    rows = varied()
    manifest = fixture_manifest(options={"spacing": 100})
    data = compile_font(manifest, rows, "ttf")
    font = TTFont(io.BytesIO(data))
    text = "the quick brown fox jumps over the lazy dog"
    ids, off = shape_run(data, text, kern=False)
    for glyph, advance in zip(ids, off):
        assert advance == font["hmtx"][font.getGlyphOrder()[glyph]][0]
    _, on = shape_run(data, text, kern=True)
    assert all(advance > 0 for advance in on)
    assert off != on


def test_run_boundaries_punctuation_and_repeats_stay_exact():
    rows = varied()
    manifest = fixture_manifest(options={"spacing": 100})
    data = compile_font(manifest, rows, "ttf")
    pairs = measure(
        {r["outline"]["codepoints"][0]: (r["outline"], "user_capture") for r in rows}, manifest
    )["pairs"]
    for text in ("AVATAR TOTAL", "a.b,c!d?e 2026", "eee lll ooo", "To Ava, WAVE Yo"):
        assert verify_shifts(data, [text], pairs) == []
    # A space carries no ink, so no rule may be created across a word boundary.
    assert not any(pair[0] == 32 or pair[1] == 32 for pair in pairs)
    # A corrected pair keeps its shift only when the letters are adjacent.
    left, right = next(iter(pairs))
    joined = (
        advances(data, chr(left) + chr(right), kern=False),
        advances(data, chr(left) + chr(right), kern=True),
    )
    spaced = (
        advances(data, chr(left) + " " + chr(right), kern=False),
        advances(data, chr(left) + " " + chr(right), kern=True),
    )
    assert joined[0][0] != joined[1][0]
    assert spaced[0] == spaced[1]
    # Repeated letters keep one glyph each and stay deterministic.
    assert advances(data, "eee", kern=True) == advances(data, "eee", kern=True)


def test_missing_glyph_keeps_visible_fallback_with_kerning_enabled():
    rows = varied()
    manifest = fixture_manifest(options={"spacing": 100})
    data = compile_font(manifest, rows, "ttf")
    ids, _ = shape_run(data, "\U0010fffd", kern=True)
    assert ids == [0]


def test_devanagari_ignores_latin_spacing_and_keeps_its_own_features():
    manifest, rows = deva_fixture()
    manifest = manifest.model_copy(
        update={"options": manifest.options.model_copy(update={"spacing": 100})}
    )
    font = TTFont(io.BytesIO(compile_font(manifest, rows, "ttf")))
    assert {r.FeatureTag for r in font["GPOS"].table.FeatureList.FeatureRecord} == {
        "abvm",
        "blwm",
    }


@pytest.mark.parametrize("format", ["ttf", "otf", "woff2"])
def test_output_is_byte_identical_across_row_order_and_process(tmp_path, format):
    rows = varied()
    manifest = fixture_manifest(options={"spacing": 100})
    data = compile_font(manifest, rows, format)
    assert data == compile_font(manifest, list(reversed(rows)), format)
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps({"manifest": manifest.model_dump(mode="json"), "rows": rows, "format": format})
    )
    code = """
import hashlib, json, sys
from penforge_contracts import FontBuildManifest
from penforge_font_engine.compiler import compile_font
value = json.load(open(sys.argv[1]))
data = compile_font(FontBuildManifest.model_validate(value["manifest"]), value["rows"], value["format"])
print(hashlib.sha256(data).hexdigest())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(source)],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH="apps/api:packages/contracts:packages/font-engine:."),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == hashlib.sha256(data).hexdigest()
    folder = tmp_path / "spacing" / format
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ("font." + format)).write_bytes(data)


def test_glyph_bounds_are_unchanged_by_kerning():
    rows = varied()
    ttf = TTFont(io.BytesIO(compile_font(fixture_manifest(options={"spacing": 100}), rows, "ttf")))
    glyphs = ttf.getGlyphSet()
    for cp, name in sorted(ttf.getBestCmap().items()):
        pen = BoundsPen(glyphs)
        glyphs[name].draw(pen)
        if cp == 32:
            assert pen.bounds is None
        else:
            assert pen.bounds is not None


@pytest.mark.parametrize("format", ["ttf", "otf", "woff2"])
@pytest.mark.parametrize("realism", [50, 100])
def test_spacing_and_authentic_variation_compose_and_validate(tmp_path, format, realism):
    from test_contextual_cycling import fixture

    rows = fixture(2 if realism == 50 else 3)
    for row in rows:
        if row["outline"]["codepoints"][0] % 2:
            row["outline"]["metrics"]["advance_width"] += 80
    manifest = fixture_manifest(options={"spacing": 100, "realism": realism})
    data = compile_font(manifest, rows, format)
    font = TTFont(io.BytesIO(data))
    assert {"GPOS", "GSUB"} <= set(font.keys())
    report = validate_font(data, manifest, rows, format, tmp_path / format)
    assert report["status"] == "passed_with_warnings", report
    shaping = report["layers"]["harfbuzz"]
    assert shaping["measured_spacing"]["verified_rule_pairs"] > 0
    raw = sfnt_bytes(data)
    assert (
        shape_run(raw, "eeeeeeee", kern=True, calt=True)[0]
        != shape_run(raw, "eeeeeeee", kern=True, calt=False)[0]
    )
    assert data == compile_font(manifest, list(reversed(rows)), format)


@pytest.mark.parametrize("format", ["ttf", "otf", "woff2"])
@pytest.mark.parametrize("include_space", [True, False])
def test_spacing_validates_actual_pairs_in_small_partial_font(tmp_path, format, include_space):
    points = (32, 66, 67) if include_space else (66, 67)
    rows = [r for r in varied() if r["outline"]["codepoints"][0] in points]
    rows[-1]["outline"]["metrics"]["advance_width"] += 100
    manifest = fixture_manifest(options={"spacing": 100})
    data = compile_font(manifest, rows, format)
    report = validate_font(data, manifest, rows, format, tmp_path / format)
    assert report["status"] == "passed_with_warnings", report
    assert report["layers"]["harfbuzz"]["measured_spacing"]["verified_rule_pairs"] > 0


def test_repeated_glyph_pairs_are_measured_and_no_new_collision_is_created():
    rows = varied()
    next(r for r in rows if r["outline"]["codepoints"] == [101])["outline"]["metrics"][
        "advance_width"
    ] += 80
    manifest = fixture_manifest(options={"spacing": 100})
    approved = {r["outline"]["codepoints"][0]: (r["outline"], "user_capture") for r in rows}
    evidence = measure(approved, manifest)
    assert evidence["pairs_considered"] == (len(rows) - 1) ** 2
    assert any(a == b for a, b in evidence["pairs"])


def test_repaired_engine_has_new_build_identity_and_rejects_unpinned_old_toolchain():
    from penforge_font_engine.compiler import installed_versions

    current = fixture_manifest(options={"spacing": 50})
    assert current.tool_versions == installed_versions()
    assert current.tool_versions["penforge-font-engine"] == "1.0.1"
    previous_tools = {
        key: value for key, value in current.tool_versions.items() if key != "penforge-font-engine"
    }
    previous = fixture_manifest(tool_versions=previous_tools, options={"spacing": 50})
    assert previous.build_id != current.build_id
    with pytest.raises(ValueError, match="toolchain_mismatch"):
        compile_font(previous, fixture_glyphs(), "ttf")
