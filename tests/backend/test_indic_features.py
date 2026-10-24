import io

import pytest
from fontTools.ttLib import TTFont
from penforge_font_engine.compiler import compile_font
from penforge_font_engine.indic import evidence_hash

from scripts.deva_fixture import fixture


def test_own_features_have_explicit_scripts_classes_and_attachments():
    manifest, rows = fixture()
    font = TTFont(io.BytesIO(compile_font(manifest, rows, "ttf")))
    for tag in ("GDEF", "GSUB", "GPOS"):
        assert tag in font
    assert {s.ScriptTag for s in font["GSUB"].table.ScriptList.ScriptRecord} == {"DFLT", "dev2"}
    assert {r.FeatureTag for r in font["GSUB"].table.FeatureList.FeatureRecord} >= {
        "nukt",
        "rphf",
        "rkrf",
        "half",
        "akhn",
    }
    assert {r.FeatureTag for r in font["GPOS"].table.FeatureList.FeatureRecord} == {"abvm", "blwm"}
    assert set(font["GDEF"].table.GlyphClassDef.classDefs.values()) == {1, 2, 3, 4}


def test_missing_or_changed_anchor_evidence_fails_closed():
    manifest, rows = fixture()
    next(r for r in rows if r["outline"]["codepoints"] == [0x915])["outline"]["indic"]["anchors"][
        "above_base"
    ]["x"] += 1
    with pytest.raises(ValueError, match="indic_evidence_hash_mismatch"):
        compile_font(manifest, rows, "ttf")
    rows[0]["outline"]["metrics"]["advance_width"] += 1
    manifest = manifest.model_copy(update={"indic_evidence_hash": evidence_hash(rows)})
    with pytest.raises(ValueError, match="anchor_geometry_hash_mismatch"):
        compile_font(manifest, rows, "ttf")
