import copy

import pytest
from penforge_contracts import GlyphOutline, content_hash
from penforge_contracts.indic import IndicEvidence
from pydantic import ValidationError
from test_glyphs_database import outline


def test_anchor_evidence_is_geometry_bound_and_never_rewrites_source():
    value = outline(0x915)
    before = copy.deepcopy(value)
    value["indic"] = IndicEvidence(
        role="base",
        source_classification="owner_engineering_record",
        geometry_hash=content_hash(before),
        anchors={"above_base": dict(x=300.0, y=700.0), "below_base": dict(x=300.0, y=0.0)},
    ).model_dump(mode="json")
    parsed = GlyphOutline.model_validate(value)
    assert parsed.codepoints == before["codepoints"]
    assert parsed.contours[0].start.y == before["contours"][0]["start"]["y"]
    assert content_hash(value) != content_hash(before)
    assert GlyphOutline.model_validate(before).model_dump(mode="json") == before


@pytest.mark.parametrize(
    "feature,role,cps",
    [
        ("rphf", "reph", [0x915, 0x94D]),
        ("half", "base", [0x915, 0x94D]),
        ("rkrf", "rakar", [0x915]),
        ("vatu", "rakar", [0x915, 0x94D, 0x930]),
    ],
)
def test_unapproved_rule_contexts_rejected(feature, role, cps):
    with pytest.raises(ValidationError):
        IndicEvidence(
            role=role,
            source_classification="owner_engineering_record",
            geometry_hash="sha256:" + "0" * 64,
            substitution=dict(feature=feature, input_codepoints=cps),
        )
