import pytest
from penforge_font_engine.compiler import compile_font
from penforge_font_engine.shaping import (
    sequence_policy,
    shape_run,
    shaping_suite,
    validate_sequence,
)

from scripts.deva_fixture import fixture


def setup(**kwargs):
    m, r = fixture(**kwargs)
    return m, r, compile_font(m, r, "ttf")


def test_all_starter_sequences_have_deterministic_traces_without_notdef():
    m, r, data = setup()
    report = shaping_suite(data, r, m)
    assert len(report["cases"]) == 11
    for row in report["cases"]:
        assert row["status"] == "passed", row
        assert 0 not in row["trace"]["glyph_ids"]
        assert row["trace"] == shape_run(data, row["codepoints"])
    a, b = [row for row in report["cases"] if row["case_id"].startswith("nukta-")]
    assert a["trace"]["codepoints"] != b["trace"]["codepoints"]
    assert a["trace"]["glyph_names"] == b["trace"]["glyph_names"]
    pre = next(x for x in report["cases"] if x["case_id"] == "prebase-i")["trace"]
    assert pre["glyph_names"][:2] == ["uni093F", "uni0915"]
    assert any(p["x_offset"] for x in report["cases"] for p in x["trace"]["positions"])


def test_reph_rakar_nukta_half_and_feature_off_are_real():
    m, r, data = setup()
    for text, feature in [
        ("क्ष", "akhn"),
        ("र्क", "rphf"),
        ("श्र", "rkrf"),
        ("क़", "nukt"),
        ("क्त", "half"),
    ]:
        cps = list(map(ord, text))
        on = shape_run(data, cps)
        off = shape_run(data, cps, {feature: False})
        assert on["glyph_ids"] != off["glyph_ids"], (text, feature, on, off)
    on = shape_run(data, list(map(ord, "कु")))
    off = shape_run(data, list(map(ord, "कु")), {"blwm": False})
    assert on["positions"] != off["positions"]


def test_explicit_half_and_visible_virama_fallback():
    m, r, data = setup(conjunct=False)
    assert validate_sequence(data, list(map(ord, "क्ष")), r, m)["fallback_kind"] == "half_form"
    m, r, data = setup(conjunct=False, half=False)
    assert validate_sequence(data, list(map(ord, "क्ष")), r, m)["fallback_kind"] == "visible_virama"
    assert validate_sequence(data, [0x915, 0x949], r, m)["status"] == "unsupported"


@pytest.mark.parametrize(
    "cps", [[0x200D], [0x915, 0x200C], [0x93C, 0x915], [0x915, 0x93C, 0x93C], [0x915, 0x202E]]
)
def test_malformed_sequences_need_review(cps):
    assert not sequence_policy(cps)


def test_joiners_preserve_source_and_general_unicode_indices():
    m, r, data = setup()
    for text in ["क्\u200dष", "क्\u200cष"]:
        cps = list(map(ord, text))
        report = validate_sequence(data, cps, r, m)
        assert report["status"] == "passed" and report["trace"]["codepoints"] == cps
    run = shape_run(data, [0x1D4B6, 0x915])
    assert run["codepoints"] == [0x1D4B6, 0x915]
    assert run["clusters"] == [0, 1] and run["glyph_ids"][0] == 0
