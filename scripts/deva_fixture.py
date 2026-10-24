"""Noto-derived OFL engineering fixture. Never user handwriting or ML evidence."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from penforge_contracts import FontBuildManifest, GlyphOutline, content_hash
from penforge_font_engine.compiler import installed_versions
from penforge_font_engine.indic import evidence_hash

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "research/fixtures/fonts/noto-deva"


class CanonicalPen(BasePen):
    def __init__(self, gs):
        super().__init__(gs)
        self.contours = []
        self.start = None
        self.segments = []

    def point(self, p):
        return dict(x=float(p[0]), y=float(p[1]))

    def _moveTo(self, p):
        self.start = self.point(p)
        self.segments = []

    def _lineTo(self, p):
        self.segments.append(dict(type="line", to=self.point(p)))

    def _curveToOne(self, a, b, c):
        self.segments.append(
            dict(type="cubic", c1=self.point(a), c2=self.point(b), to=self.point(c))
        )

    def _qCurveToOne(self, a, b):
        self.segments.append(dict(type="quadratic", c=self.point(a), to=self.point(b)))

    def _closePath(self):
        if self.segments[-1]["to"] != self.start:
            self.segments.append(dict(type="line", to=self.start))
        self.contours.append(dict(closed=True, start=self.start, segments=self.segments))

    def _endPath(self):
        raise ValueError("open_reference_outline")


def fixture(*, half=True, conjunct=True):
    provenance = json.loads((REFERENCE / "provenance.json").read_bytes())
    for row in provenance["files"]:
        assert hashlib.sha256((REFERENCE / row["path"]).read_bytes()).hexdigest() == row["sha256"]
    f = TTFont(REFERENCE / "NotoSansDevanagari.ttf")
    gs = f.getGlyphSet()
    cmap = f.getBestCmap()
    rows = []

    def row(cps, name, role="base", feature=None, mapping="cmap"):
        pen = CanonicalPen(gs)
        gs[name].draw(pen)
        bounds = BoundsPen(gs)
        gs[name].draw(bounds)
        bbox = bounds.bounds
        aw, lsb = f["hmtx"][name]
        if role in {"mark", "reph"}:
            aw = 0
        o = dict(
            schema_version=1,
            units_per_em=1000,
            coordinate_system=dict(origin="baseline", x_positive="right", y_positive="up"),
            codepoints=cps,
            mapping=mapping,
            fill_rule="evenodd",
            metrics=dict(advance_width=float(aw), left_side_bearing=float(lsb)),
            contours=pen.contours,
        )
        anchors = {}
        if role in {"base", "conjunct", "rakar", "half"} and cps != [32]:
            anchors = dict(above_base=dict(x=aw / 2, y=720.0), below_base=dict(x=aw / 2, y=0.0))
        elif role in {"mark", "reph"}:
            above = cps in ([0x901], [0x902], [0x930, 0x94D])
            anchors = {
                ("above_mark" if above else "below_mark"): dict(
                    x=(bbox[0] + bbox[2]) / 2, y=float(bbox[1] if above else bbox[3])
                )
            }
            if above:
                anchors["stack_above_base"] = dict(x=(bbox[0] + bbox[2]) / 2, y=float(bbox[3] + 30))
        o["indic"] = dict(
            schema_version=1,
            role=role,
            source_classification="engineering_fixture",
            geometry_hash=content_hash(o),
            anchors=anchors,
            substitution=dict(feature=feature, input_codepoints=cps) if feature else None,
        )
        o = GlyphOutline.model_validate(o).model_dump(mode="json")
        rows.append(dict(outline=o, approval="approved", origin="user_capture", variant_index=0))

    cps = {
        0x915,
        0x937,
        0x930,
        0x936,
        0x924,
        0x939,
        0x93C,
        0x94D,
        0x93F,
        0x941,
        0x942,
        0x943,
        0x901,
        0x902,
        0x958,
        32,
    }
    for cp in sorted(cps):
        row(
            [cp],
            cmap[cp],
            "mark" if cp in {0x93C, 0x94D, 0x941, 0x942, 0x943, 0x901, 0x902} else "base",
        )
    row([0x915, 0x93C], "uni0958", "base", "nukt", "gsub_only")
    row([0x930, 0x94D], "uni0930094D", "reph", "rphf", "gsub_only")
    row([0x936, 0x94D, 0x930], "uni0936094D0930", "rakar", "rkrf", "gsub_only")
    if half:
        row([0x915, 0x94D], "uni0915094D", "half", "half", "gsub_only")
    if conjunct:
        row([0x915, 0x94D, 0x937], "uni0915094D0937", "conjunct", "akhn", "gsub_only")
    manifest = FontBuildManifest(
        snapshot_hash=content_hash(rows),
        snapshot_created_at=datetime(2026, 10, 20, tzinfo=UTC),
        family_name="Penforge Indic Engineering Fixture",
        script_code="Deva",
        indic_evidence_hash=evidence_hash(rows),
        validation_profile="penforge-devanagari-v1",
        tool_versions=installed_versions(),
        options=dict(
            descender=-500,
            ascender=1300,
            license_description="Noto-derived engineering fixture. Copyright 2022 The Noto Project Authors. SIL Open Font License 1.1. Not handwriting.",
        ),
    )
    return manifest, rows
