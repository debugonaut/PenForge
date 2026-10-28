import hashlib
import io

import pytest
from font_fixtures import fixture_glyphs, fixture_manifest
from fontTools.ttLib import TTFont
from penforge_font_engine.compiler import compile_font, coverage


@pytest.mark.parametrize("format", ["ttf", "otf", "woff2"])
@pytest.mark.parametrize("full", [True, False])
def test_actual_formats_curves_holes_descenders_coverage_and_reproducibility(format, full):
    m = fixture_manifest()
    rows = fixture_glyphs(full, nonbmp=True)
    data = compile_font(m, rows, format)
    font = TTFont(io.BytesIO(data))
    assert set(font.getBestCmap()) == {r["outline"]["codepoints"][0] for r in rows}
    assert {t.format for t in font["cmap"].tables} >= {4, 12}
    assert ("CFF " in font) == (format == "otf")
    assert ("glyf" in font) == (format != "otf")
    bounds = font.getGlyphSet()[font.getBestCmap()[65]]
    assert bounds.width == 620 and font["hhea"].descent == -300
    if format != "otf":
        g = font["glyf"][font.getBestCmap()[65]]
        assert g.numberOfContours == 2 and g.yMin == -180
        assert font["glyf"][".notdef"].numberOfContours == 2
    report = coverage(rows, m, validated=True)
    assert report["complete_english"] == full
    assert report["core_letters_mapped"] == 52
    assert len(report["missing"]) == (0 if full else 43)
    assert (
        hashlib.sha256(data).digest()
        == hashlib.sha256(compile_font(m, list(reversed(rows)), format)).digest()
    )
    assert data != compile_font(fixture_manifest(style_name="Changed"), rows, format)


def test_pre_security_update_manifest_is_never_reinterpreted():
    manifest = fixture_manifest()
    manifest.tool_versions["brotli"] = "1.2.0"
    with pytest.raises(ValueError, match="^toolchain_mismatch$"):
        compile_font(manifest, fixture_glyphs(False), "woff2")


def test_unapproved_and_generated_are_distinct():
    rows = fixture_glyphs(False)
    extra = fixture_glyphs()[0]
    extra["approval"] = "pending"
    extra["origin"] = "ml_generated"
    rows.append(extra)
    rows[0]["origin"] = "ml_generated"
    report = coverage(rows, fixture_manifest())
    assert report["mapped"] == 52 and report["generated_pending"] == [[32]]
    assert report["approved_generated"] == [[65]]
    assert not report["complete_english"]


@pytest.mark.parametrize(
    "change", ["nan", "open", "area", "upm", "bounds", "nodes", "metrics", "toolchain", "deva"]
)
def test_invalid_geometry_and_toolchain_fail_closed(change):
    rows = fixture_glyphs(False)
    m = fixture_manifest()
    o = rows[0]["outline"]
    if change == "nan":
        o["contours"][0]["start"]["x"] = float("nan")
    if change == "open":
        o["contours"][0]["closed"] = False
    if change == "area":
        for c in o["contours"]:
            c["start"] = {"x": 0, "y": 0}
            c["segments"] = [{"type": "line", "to": {"x": 0, "y": 0}}]
    if change == "upm":
        o["units_per_em"] = 2000
    if change == "bounds":
        o["contours"][0]["segments"][0]["c1"]["y"] = -301
    if change == "nodes":
        o["contours"] *= 129
    if change == "metrics":
        o["metrics"]["advance_width"] = 0.0
    if change == "toolchain":
        m = fixture_manifest(tool_versions={"wrong": "1"})
    if change == "deva":
        o["codepoints"] = [0x915]
    with pytest.raises(ValueError):
        compile_font(m, rows, "ttf")
