"""Scalar-indexed HarfBuzz engineering reports; no recognition/linguistic claims."""

import hashlib
import io
import json
from pathlib import Path

import uharfbuzz as hb
from fontTools.ttLib import TTFont
from penforge_contracts import content_hash
from penforge_contracts.unicode import normalize

from .compiler import sfnt_bytes
from .indic import forms, glyph_name

STARTER = Path(__file__).resolve().parents[3] / "research/evaluation/corpora/deva-starter-v1.yaml"


def shape_run(data, cps, features=None):
    raw = sfnt_bytes(data)
    f = TTFont(io.BytesIO(raw))
    font = hb.Font(hb.Face(raw))
    font.scale = (f["head"].unitsPerEm,) * 2
    b = hb.Buffer()
    b.add_codepoints(cps)
    b.script = "Deva"
    b.language = "hi"
    b.direction = "ltr"
    b.cluster_level = hb.BufferClusterLevel.MONOTONE_GRAPHEMES
    hb.shape(font, b, features or {})
    return dict(
        codepoints=cps,
        normalization_mode="preserve_input",
        unicode_version="17.0.0",
        derived_normalization=dict(
            mode="NFD", codepoints=normalize(cps, "NFD"), policy="ucd17-derived-evidence-v1"
        ),
        default_features="HarfBuzz Indic shaping defaults",
        harfbuzz_version=hb.version_string(),
        script="Deva",
        opentype_script="dev2",
        language="hi",
        direction="ltr",
        cluster_units="unicode_scalars",
        cluster_level="MONOTONE_GRAPHEMES",
        enabled_features=features or {},
        font_sha256=hashlib.sha256(data).hexdigest(),
        glyph_ids=[i.codepoint for i in b.glyph_infos],
        glyph_names=[f.getGlyphName(i.codepoint) for i in b.glyph_infos],
        clusters=[i.cluster for i in b.glyph_infos],
        positions=[
            dict(
                x_advance=p.x_advance,
                y_advance=p.y_advance,
                x_offset=p.x_offset,
                y_offset=p.y_offset,
            )
            for p in b.glyph_positions
        ],
    )


def supported_input(cps, cmap):
    normalized = normalize(cps, "NFD")
    return all(cp in cmap or cp in {0x200C, 0x200D} for cp in normalized)


def sequence_policy(cps):
    if not cps:
        return False
    for i, cp in enumerate(cps):
        if cp in {0x200C, 0x200D} and (not i or cps[i - 1] != 0x94D):
            return False
        if cp in {0x93C, 0x94D} and (not i or cps[i - 1] in {0x93C, 0x94D}):
            return False
        if 0x202A <= cp <= 0x202E or 0x2066 <= cp <= 0x2069:
            return False
    return True


def validate_sequence(data, cps, rows, manifest):
    f = TTFont(io.BytesIO(sfnt_bytes(data)))
    cmap = f.getBestCmap()
    base = dict(
        codepoints=cps,
        status="unsupported",
        fallback_only=False,
        reason="capture_or_review_missing_characters",
    )
    if not sequence_policy(cps):
        return base | dict(reason="sequence_requires_review")
    if not supported_input(cps, cmap):
        return base
    run = shape_run(data, cps)
    if not run["glyph_ids"] or 0 in run["glyph_ids"]:
        return base | dict(reason="missing_shaped_character", trace=run)
    if sum(p["x_advance"] for p in run["positions"]) < 0 or any(
        not 0 <= c < len(cps) for c in run["clusters"]
    ):
        raise ValueError("invalid_shaped_clusters_or_advance")
    # Positioned marks must carry explicit anchors. Substituted full forms need none.
    all_forms = forms(rows, manifest)
    evidence = {glyph_name(r): r["outline"].get("indic") for r in all_forms}
    evidence.update(
        {
            cmap[r["outline"]["codepoints"][0]]: r["outline"].get("indic")
            for r in rows
            if r.get("approval") == "approved"
            and r["outline"]["mapping"] == "cmap"
            and r["outline"]["codepoints"][0] in cmap
        }
    )
    classes = f["GDEF"].table.GlyphClassDef.classDefs if "GDEF" in f else {}
    for name in run["glyph_names"]:
        if classes.get(name) == 3 and not (evidence.get(name) or {}).get("anchors"):
            return base | dict(reason="mark_attachment_needs_review", trace=run)
    for name in run["glyph_names"]:
        item = evidence.get(name) or {}
        a = item.get("anchors", {})
        for mark_key, base_key in (("above_mark", "above_base"), ("below_mark", "below_base")):
            if mark_key in a and not any(
                base_key in (evidence.get(other) or {}).get("anchors", {})
                or (
                    mark_key == "above_mark"
                    and "stack_above_base" in (evidence.get(other) or {}).get("anchors", {})
                )
                for other in run["glyph_names"]
                if other != name
            ):
                return base | dict(reason="base_attachment_needs_review", trace=run)
    # Marks without classification/evidence also cannot acquire coverage by counting.
    marks = {0x901, 0x902, 0x903, 0x93C, 0x94D, *range(0x940, 0x949)}
    for cp in set(normalize(cps, "NFD")) & marks:
        if (
            cp in cmap
            and cmap[cp] in run["glyph_names"]
            and not (evidence.get(cmap[cp]) or {}).get("anchors")
        ):
            return base | dict(reason="mark_attachment_needs_review", trace=run)
    fallback = None
    if 0x94D in cps:
        if any((evidence.get(n) or {}).get("role") == "half" for n in run["glyph_names"]):
            fallback = "half_form"
        elif cmap.get(0x94D) in run["glyph_names"]:
            fallback = "visible_virama"
    return base | dict(
        status="passed",
        fallback_only=fallback is not None,
        reason=None,
        fallback_kind=fallback,
        trace=run,
        output_hash=content_hash(run),
    )


def shaping_suite(data, rows, manifest):
    f = TTFont(io.BytesIO(sfnt_bytes(data)))
    cases = []
    for case in json.loads(STARTER.read_bytes())["cases"]:
        result = validate_sequence(data, case["codepoints"], rows, manifest) | dict(
            case_id=case["case_id"], text=case["text"]
        )
        if result["status"] == "passed":
            result["feature_off"] = {
                tag: shape_run(data, case["codepoints"], {tag: False})
                for tag in case["features_to_inspect"]
            }
        cases.append(result)
    features = sorted(
        {
            r.FeatureTag
            for tag in ("GSUB", "GPOS")
            if tag in f
            for r in f[tag].table.FeatureList.FeatureRecord
        }
    )
    return dict(
        status="passed",
        profile="penforge-devanagari-starter-v1",
        harfbuzz_version=hb.version_string(),
        features=features,
        cases=cases,
        common_word_inventory=False,
        linguistic_approval=False,
        production_validated=False,
    )
