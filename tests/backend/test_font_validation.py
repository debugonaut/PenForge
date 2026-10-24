import pytest
from font_fixtures import fixture_glyphs, fixture_manifest
from penforge_font_engine import compile_font
from penforge_font_engine import validation as v


@pytest.mark.parametrize("format", ["ttf", "otf", "woff2"])
@pytest.mark.parametrize("full", [True, False])
def test_every_real_validator_executes(format, full, tmp_path):
    m = fixture_manifest()
    rows = fixture_glyphs(full, nonbmp=True)
    data = compile_font(m, rows, format)
    report = v.validate_font(data, m, rows, format, tmp_path)
    assert report["status"] == ("passed" if full else "passed_with_warnings")
    assert all(x["status"] == "passed" for x in report["layers"].values())
    assert report["coverage"]["complete_english"] == full
    assert report["layers"]["harfbuzz"]["fallback_glyph_ids"] == [0]
    assert (tmp_path / "render.png").exists()


@pytest.mark.parametrize(
    "layer", ["check_structure", "check_ots", "check_fontbakery", "check_shaping", "check_render"]
)
def test_required_failure_or_missing_validator_never_passes(monkeypatch, tmp_path, layer):
    def failed(*args, **kwargs):
        raise RuntimeError("private font name never exposed")

    m = fixture_manifest()
    rows = fixture_glyphs(False)
    data = compile_font(m, rows, "ttf")
    monkeypatch.setattr(v, layer, failed)
    report = v.validate_font(data, m, rows, "ttf", tmp_path)
    assert report["status"] == "failed"
    assert "private font name" not in str(report)
    assert not report["coverage"]["complete_english"]


def test_structural_checksum_corruption_cannot_pass(tmp_path):
    m = fixture_manifest()
    rows = fixture_glyphs()
    data = bytearray(compile_font(m, rows, "ttf"))
    data[-1] ^= 1
    report = v.validate_font(bytes(data), m, rows, "ttf", tmp_path)
    assert report["status"] == "failed" and report["layers"]["structural"]["status"] == "failed"
