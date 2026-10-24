"""Canonical even-odd outlines to real glyf/CFF/WOFF2 tables."""

from __future__ import annotations

import importlib.metadata
import io
import math
from datetime import UTC

import pathops
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from penforge_contracts import FontBuildManifest, GlyphOutline

from .alternates import cycling_source, enabled_alternates
from .spacing import kerning_source
from .spacing import measure as measure_spacing

ENGINE_VERSION = "1.0.1"

TOOLS = (
    "fonttools",
    "brotli",
    "uharfbuzz",
    "opentype-sanitizer",
    "fontbakery",
    "skia-pathops",
    "freetype-py",
    "pillow",
)


def installed_versions() -> dict[str, str]:
    return {
        **{name: importlib.metadata.version(name) for name in TOOLS},
        "penforge-font-engine": ENGINE_VERSION,
    }


def draw(outline, pen):
    for contour in outline["contours"]:
        draw_contour(contour, pen)


def xy(point):
    return (point["x"], point["y"])


def draw_contour(contour, pen):
    pen.moveTo(xy(contour["start"]))
    for segment in contour["segments"]:
        if segment["type"] == "line":
            pen.lineTo(xy(segment["to"]))
        elif segment["type"] == "quadratic":
            pen.qCurveTo(xy(segment["c"]), xy(segment["to"]))
        else:
            pen.curveTo(xy(segment["c1"]), xy(segment["c2"]), xy(segment["to"]))
    pen.closePath()


def normalized(outline, *, clockwise, tolerance):
    """Resolve even-odd intersections/holes; never thicken overlapping strokes.

    PathOps uses float32 coordinates; reject inputs outside its explicit precision
    budget. Contour area is checked BEFORE simplification, never silently dropped.
    """
    path = pathops.Path()
    path.fillType = pathops.FillType.EVEN_ODD
    for contour in outline["contours"]:
        area = AreaPen(None)
        draw_contour(contour, area)
        if abs(area.value) < 0.01:
            raise ValueError("degenerate_contour")
        draw_contour(contour, path.getPen())
    if not outline["contours"]:
        return RecordingPen()
    clean = pathops.simplify(
        path, fix_winding=True, keep_starting_points=False, clockwise=clockwise
    )
    if not math.isfinite(clean.area) or abs(clean.area) < 0.01:
        raise ValueError("degenerate_filled_geometry")
    if len(list(clean)) > 16384:
        raise ValueError("normalized_complexity_exceeded")
    before, after = BoundsPen(None), BoundsPen(None)
    path.draw(before)
    clean.draw(after)
    if (
        before.bounds is None
        or after.bounds is None
        or any(abs(a - b) > tolerance for a, b in zip(before.bounds, after.bounds))
    ):
        raise ValueError("normalization_exceeds_tolerance")
    result = RecordingPen()
    clean.draw(result)
    return result


def approved_glyphs(glyphs, manifest):
    result = {}
    for row in glyphs:
        if row.get("approval") != "approved" or row.get("variant_index", 0) != 0:
            continue
        outline = GlyphOutline.model_validate(row["outline"]).model_dump(mode="json")
        if outline["mapping"] != "cmap":
            continue
        cp = outline["codepoints"][0]
        if 0x900 <= cp <= 0x97F and manifest.script_code != "Deva":
            raise ValueError("shaping_unvalidated")
        if cp in result:
            raise ValueError("duplicate_mapping")
        if outline["units_per_em"] != manifest.options.units_per_em:
            raise ValueError("inconsistent_upm")
        metrics = outline["metrics"]
        if (
            not (
                0 <= metrics["advance_width"] <= 32767
                if manifest.script_code == "Deva"
                else 0 < metrics["advance_width"] <= 32767
            )
            or not -32768 <= metrics["left_side_bearing"] <= 32767
        ):
            raise ValueError("metric_out_of_policy")
        for contour in outline["contours"]:
            for point in [contour["start"]] + [
                v for s in contour["segments"] for k, v in s.items() if k != "type"
            ]:
                if (
                    not -32700 <= point["x"] <= 32700
                    or not manifest.options.descender <= point["y"] <= manifest.options.ascender
                ):
                    raise ValueError("coordinate_out_of_policy")
        result[cp] = (outline, row.get("origin", "user_capture"))
    if not result or not any(cp != 32 for cp in result):
        raise ValueError("no_approved_drawn_glyphs")
    return result


def coverage(glyphs, manifest, *, validated=False, shaping=None):
    if manifest.script_code == "Deva":
        from penforge_contracts.devanagari import coverage_evidence

        return coverage_evidence(glyphs, shaping=shaping)
    approved = approved_glyphs(glyphs, manifest)
    alternate_rows = enabled_alternates(glyphs, manifest)
    spacing = measure_spacing(approved, manifest)
    mapped = sorted(approved)
    missing = sorted(set(range(32, 127)) - set(mapped))
    generated = [
        cp
        for cp, (_, origin) in approved.items()
        if origin in {"ml_generated", "geometric_reconstruction", "style_conditioned_generation"}
    ]
    pending = sorted(
        {
            row["outline"]["codepoints"][0]
            for row in glyphs
            if row.get("approval") == "pending"
            and row.get("origin")
            in {"ml_generated", "geometric_reconstruction", "style_conditioned_generation"}
        }
    )
    return dict(
        script_code="Latn",
        required_set_id="latin-printable-ascii-95-v1",
        mapped=len(mapped),
        mapped_codepoints=mapped,
        missing=[[cp] for cp in missing],
        generated_pending=[[cp] for cp in pending],
        approved_generated=[[cp] for cp in sorted(generated)],
        unvalidated_features=(["calt"] if not alternate_rows else [])
        + ["ligatures", "joins", "Devanagari"]
        + (["measured_kerning"] if spacing["status"] == "applied" else []),
        warnings=(["missing_glyphs"] if missing else [])
        + (["approved_generated_glyphs"] if generated else [])
        + (["calt_unsupported_in_some_apps"] if alternate_rows else [])
        + (["kern_unsupported_in_some_apps"] if spacing["status"] == "applied" else []),
        complete_english=not missing and validated,
        core_letters_mapped=len(set(mapped) & (set(range(65, 91)) | set(range(97, 123)))),
        contextual_alternates={
            "policy": "adjacent-repeat-v1",
            "realism": manifest.options.realism,
            "enabled_codepoints": sorted(alternate_rows),
            "maximum_set_size": max((len(r) + 1 for r in alternate_rows.values()), default=1),
            "engineering_invariants_passed": validated,
            "handwriting_fidelity": None,
        },
        spacing={
            "policy": spacing["policy"],
            "status": spacing["status"],
            "control": spacing["control"],
            "amplitude_units": spacing["amplitude_units"],
            "corrected_pairs": spacing.get("corrected_pairs", 0),
            "pairs_considered": spacing["pairs_considered"],
            "target_gap": spacing.get("target_gap"),
            "max_abs_shift": spacing.get("max_abs_shift", 0),
            "handwriting_fidelity": None,
        },
    )


def notdef():
    def contour(points):
        return dict(
            start=dict(x=points[0][0], y=points[0][1]),
            closed=True,
            segments=[dict(type="line", to=dict(x=x, y=y)) for x, y in points[1:] + points[:1]],
        )

    return dict(
        contours=[
            contour([(50, 0), (550, 0), (550, 700), (50, 700)]),
            contour([(110, 60), (490, 60), (490, 640), (110, 640)]),
        ]
    )


def compile_font(manifest: FontBuildManifest, glyphs: list[dict], format: str) -> bytes:
    if manifest.tool_versions != installed_versions():
        raise ValueError("toolchain_mismatch")
    if format not in {"ttf", "otf", "woff2"}:
        raise ValueError("unsupported_format")
    approved = approved_glyphs(glyphs, manifest)
    alternate_rows = enabled_alternates(glyphs, manifest)
    names = {
        cp: "space" if cp == 32 else f"uni{cp:04X}" if cp <= 0xFFFF else f"u{cp:05X}"
        for cp in sorted(approved)
    }
    # A metrics-only space exists only when approved; an unapproved space never maps.
    order = (
        [".notdef"]
        + (["space"] if 32 in names else [])
        + [name for cp, name in names.items() if cp != 32]
    )
    spacing = measure_spacing(approved, manifest) if manifest.script_code != "Deva" else None
    spacing_pairs = spacing["pairs"] if spacing else {}
    options = manifest.options
    is_ttf = format != "otf"
    fb = FontBuilder(options.units_per_em, isTTF=is_ttf)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(names)
    outlines = {".notdef": notdef()} | {names[cp]: outline for cp, (outline, _) in approved.items()}
    metrics = {".notdef": (600, 50)} | {
        names[cp]: (
            otRound(outline["metrics"]["advance_width"]),
            otRound(outline["metrics"]["left_side_bearing"]),
        )
        for cp, (outline, _) in approved.items()
    }
    groups = {}
    for cp, rows in alternate_rows.items():
        groups[cp] = [names[cp]]
        for i, row in enumerate(rows, 1):
            # Reuse every base geometry/metric/script check for GSUB-only entries.
            validated = approved_glyphs([row | {"variant_index": 0}], manifest)[cp][0]
            name = f"{names[cp]}.alt{i}"
            groups[cp].append(name)
            order.append(name)
            outlines[name] = validated
            metrics[name] = (
                otRound(validated["metrics"]["advance_width"]),
                otRound(validated["metrics"]["left_side_bearing"]),
            )
    indic_count = 0
    if manifest.script_code == "Deva":
        from .indic import forms, glyph_name

        for row in forms(glyphs, manifest):
            name = glyph_name(row)
            o = row["outline"]
            order.append(name)
            outlines[name] = o
            metrics[name] = (
                otRound(o["metrics"]["advance_width"]),
                otRound(o["metrics"]["left_side_bearing"]),
            )
            indic_count += 1
    fb.setupGlyphOrder(order)
    compiled = {}
    for name in order:
        recording = normalized(
            outlines[name], clockwise=is_ttf, tolerance=options.conversion_tolerance
        )
        if is_ttf:
            pen = TTGlyphPen(None)
            # Total tolerance includes at most sqrt(.5^2+.5^2) coordinate rounding.
            recording.replay(
                Cu2QuPen(pen, max_err=options.conversion_tolerance - 0.71, reverse_direction=False)
            )
            compiled[name] = pen.glyph()
        else:
            cff_pen = T2CharStringPen(metrics[name][0], None, roundTolerance=0)
            recording.replay(cff_pen)
            compiled[name] = cff_pen.getCharString()
    if is_ttf:
        fb.setupGlyf(compiled)
    else:
        fb.setupCFF(
            manifest.postscript_name,
            {
                "FullName": manifest.family_name + " " + manifest.style_name,
                "FamilyName": manifest.family_name,
                "Weight": manifest.style_name,
                "version": "1.000",
            },
            compiled,
            {},
        )
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(
        ascent=options.ascender, descent=options.descender, lineGap=options.line_gap
    )
    fb.setupNameTable(
        dict(
            familyName=manifest.family_name,
            styleName=manifest.style_name,
            uniqueFontIdentifier=manifest.build_id,
            fullName=manifest.family_name + " " + manifest.style_name,
            version="Version 1.000",
            psName=manifest.postscript_name,
            licenseDescription=options.license_description,
            copyright="Private Penforge font",
        )
    )
    fb.setupOS2(
        version=4,
        sTypoAscender=options.ascender,
        sTypoDescender=options.descender,
        sTypoLineGap=options.line_gap,
        usWinAscent=options.ascender,
        usWinDescent=-options.descender,
        fsType=options.fs_type,
        fsSelection=0xC0,
        usWeightClass=400,
        usWidthClass=5,
    )
    fb.setupPost(keepGlyphNames=True)
    fb.setupMaxp()
    if manifest.script_code != "Deva":
        feature_source = kerning_source(spacing_pairs, names) + "\n" + cycling_source(groups)
        if feature_source.strip():
            addOpenTypeFeaturesFromString(
                fb.font,
                "languagesystem DFLT dflt;\nlanguagesystem latn dflt;\n" + feature_source,
            )
    if manifest.script_code == "Deva":
        from .indic import add_features

        add_features(fb.font, glyphs, manifest, names)
    if is_ttf:
        gasp = newTable("gasp")
        gasp.gaspRange = {65535: 15}
        fb.font["gasp"] = gasp
    # FontBuilder's current-time defaults are overwritten before any serialization.
    timestamp = int(manifest.snapshot_created_at.astimezone(UTC).timestamp()) + 2082844800
    if not 0 <= timestamp <= 0xFFFFFFFF:
        raise ValueError("timestamp_out_of_policy")
    fb.font["head"].created = fb.font["head"].modified = timestamp
    fb.font.recalcTimestamp = False
    fb.font.flavor = "woff2" if format == "woff2" else None
    out = io.BytesIO()
    fb.font.save(out, reorderTables=True)
    data = out.getvalue()
    structural(
        data,
        manifest,
        format,
        set(approved),
        sum(len(r) for r in alternate_rows.values()) + indic_count,
    )
    return data


def sfnt_bytes(data):
    font = TTFont(io.BytesIO(data), recalcTimestamp=False, checkChecksums=2)
    if font.flavor:
        font.flavor = None
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()
    return data


def structural(data, manifest, format, expected, alternate_count=0):
    signature = {"ttf": b"\x00\x01\x00\x00", "otf": b"OTTO", "woff2": b"wOF2"}[format]
    if data[:4] != signature:
        raise ValueError("format_signature_mismatch")
    font = TTFont(io.BytesIO(data), recalcTimestamp=False, checkChecksums=2)
    required = {"head", "hhea", "maxp", "OS/2", "hmtx", "cmap", "name", "post"} | (
        {"CFF "} if format == "otf" else {"glyf", "loca", "gasp"}
    )
    if not required <= set(font.keys()) or set(font.getBestCmap()) != expected:
        raise ValueError("invalid_tables_or_cmap")
    if (
        font["maxp"].numGlyphs != len(expected) + 1 + alternate_count
        or font.getGlyphOrder()[0] != ".notdef"
    ):
        raise ValueError("glyph_count_mismatch")
    if (font["hhea"].ascent, font["hhea"].descent, font["hhea"].lineGap) != (
        manifest.options.ascender,
        manifest.options.descender,
        manifest.options.line_gap,
    ):
        raise ValueError("metric_mismatch")
    for tag in font.keys():
        if tag != "GlyphOrder":
            font[tag]  # force full decompilation, including all required tables
    buf = io.BytesIO()
    font.save(buf)
    reopened = TTFont(io.BytesIO(buf.getvalue()), recalcTimestamp=False)
    if (
        reopened.getBestCmap() != font.getBestCmap()
        or reopened["hmtx"].metrics != font["hmtx"].metrics
    ):
        raise ValueError("roundtrip_mismatch")
