"""Five mandatory validator layers. Reports never quote font names or writing."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import subprocess
import sys
from pathlib import Path

import ots
import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

from .alternates import enabled_alternates, validate_cycling
from .compiler import approved_glyphs, coverage, sfnt_bytes, structural
from .spacing import RUNS as SPACING_RUNS
from .spacing import measure as measure_spacing
from .spacing import run_gaps, summarize, verify_shifts

CHECKS = {"required_tables", "sfnt_version", "name/char_restrictions", "os2_metrics_match_hhea"}
LAYERS = ("structural", "ots", "fontbakery", "harfbuzz", "render")


def check_structure(data, manifest, format, rows):
    approved = approved_glyphs(rows, manifest)
    structural(
        data,
        manifest,
        format,
        set(approved),
        sum(len(r) for r in enabled_alternates(rows, manifest).values())
        + (
            len(__import__("penforge_font_engine.indic", fromlist=["forms"]).forms(rows, manifest))
            if manifest.script_code == "Deva"
            else 0
        ),
    )
    raw = sfnt_bytes(data)
    padding = b"\0" * ((-len(raw)) % 4)
    if (
        sum(struct.unpack(">" + str((len(raw) + len(padding)) // 4) + "I", raw + padding))
        & 0xFFFFFFFF
        != 0xB1B0AFBA
    ):
        raise ValueError("font_checksum_failed")
    f = TTFont(io.BytesIO(raw), recalcTimestamp=False)
    for tag, entry in f.reader.tables.items():
        from fontTools.ttLib.sfnt import calcChecksum

        table = raw[entry.offset : entry.offset + entry.length]
        if tag == "head":
            table = table[:8] + b"\0\0\0\0" + table[12:]
        if calcChecksum(table) != entry.checkSum:
            raise ValueError("table_checksum_failed")
    if (
        f["OS/2"].fsType != manifest.options.fs_type
        or f["OS/2"].sTypoAscender != f["hhea"].ascent
        or f["OS/2"].sTypoDescender != f["hhea"].descent
    ):
        raise ValueError("metric_mismatch")
    glyph_set = f.getGlyphSet()
    for cp, (outline, _) in approved.items():
        name = f.getBestCmap()[cp]
        pen = BoundsPen(glyph_set)
        glyph_set[name].draw(pen)
        aw, lsb = f["hmtx"][name]
        if (
            abs(aw - outline["metrics"]["advance_width"]) > 0.5
            or abs(lsb - outline["metrics"]["left_side_bearing"]) > 0.5
        ):
            raise ValueError("bearing_or_advance_mismatch")
        if cp == 32:
            if pen.bounds is not None:
                raise ValueError("space_has_outline")
        elif (
            pen.bounds is None
            or pen.bounds[1] < manifest.options.descender
            or pen.bounds[3] > manifest.options.ascender
        ):
            raise ValueError("invalid_glyph_bounds")
    return {
        "status": "passed",
        "tables": sorted(f.keys()),
        "glyph_count": len(f.getGlyphOrder()),
        "checksum": "verified",
        "cmap": sorted(f.getBestCmap()),
    }


def check_ots(path, working):
    result = subprocess.run(
        [ots.OTS_SANITIZE, str(path), str(working / "sanitized.sfnt")],
        capture_output=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise ValueError("ots_rejected")
    reopened = TTFont(working / "sanitized.sfnt")
    original = TTFont(path)
    if reopened.getBestCmap() != original.getBestCmap():
        raise ValueError("ots_cmap_changed")
    return {
        "status": "passed",
        "binary_version": "9.2.0",
        "sanitized_sha256": hashlib.sha256((working / "sanitized.sfnt").read_bytes()).hexdigest(),
    }


def check_fontbakery(path, working):
    output = working / "fontbakery.json"
    raw = sfnt_bytes(path.read_bytes())
    suffix = ".otf" if raw[:4] == b"OTTO" else ".ttf"
    normalized_path = working / ("validated" + suffix)
    normalized_path.write_bytes(raw)
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("_fontbakery_child.py")),
            str(normalized_path),
            str(output),
        ],
        capture_output=True,
        timeout=25,
    )
    if not output.exists() or output.stat().st_size > 1024 * 1024:
        raise ValueError("fontbakery_unavailable")
    result = json.loads(output.read_text())
    checks = [check for section in result["sections"] for check in section["checks"]]
    found = {
        check["key"][1].removeprefix("<FontBakeryCheck:").removesuffix(">") for check in checks
    }
    if (
        found != CHECKS
        or len(checks) != len(CHECKS)
        or process.returncode
        or any(check["result"] not in {"PASS", "INFO", "WARN"} for check in checks)
    ):
        raise ValueError("fontbakery_policy_failed")
    # Raw messages may include private names: persist coded statuses only.
    return {
        "status": "passed",
        "profile": "penforge-latin-v1",
        "checks": {check["key"][1]: check["result"] for check in checks},
        "warnings": [check["module"] for check in checks if check["result"] == "WARN"],
    }


def shape(font, text):
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = "ltr"
    buf.script = "Latn"
    buf.language = "en"
    hb.shape(font, buf, {"liga": False, "calt": False, "kern": False})
    return [x.codepoint for x in buf.glyph_infos], [x.x_advance for x in buf.glyph_positions]


def spacing_report(raw, rows, manifest):
    """Before/after gap measurements for the spacing feature, kept separate.

    These are ink gaps in font units. They say nothing about legibility or how
    much the result resembles the writer's hand.
    """
    evidence = measure_spacing(approved_glyphs(rows, manifest), manifest)
    # Every emitted rule is exercised, including partial coverage absent from RUNS.
    runs = tuple(SPACING_RUNS) + tuple(chr(a) + chr(b) for a, b in evidence["pairs"])
    before = summarize(
        run_gaps(raw, SPACING_RUNS, kern=False), units_per_em=manifest.options.units_per_em
    )
    after = summarize(
        run_gaps(raw, SPACING_RUNS, kern=True), units_per_em=manifest.options.units_per_em
    )
    if evidence["status"] == "applied":
        if before["samples"] != after["samples"]:
            raise ValueError("spacing_sample_count_changed")
        before_gaps, after_gaps = run_gaps(raw, runs, kern=False), run_gaps(raw, runs, kern=True)
        if any(a < 0 <= b for b, a in zip(before_gaps, after_gaps)):
            raise ValueError("spacing_created_ink_collision")
        for calt in (False, True):
            if verify_shifts(raw, runs, evidence["pairs"], calt=calt):
                raise ValueError("spacing_contextual_shift_mismatch")
        population = evidence["population"]
        if population["after"]["stdev"] > population["before"]["stdev"] + 0.5:
            raise ValueError("spacing_increased_population_variance")
    return {
        "policy": evidence["policy"],
        "status": evidence["status"],
        "control": evidence["control"],
        "amplitude_units": evidence["amplitude_units"],
        "corrected_pairs": evidence.get("corrected_pairs", 0),
        "uncorrected_outliers": evidence.get("uncorrected_outliers", 0),
        "max_abs_shift": evidence.get("max_abs_shift", 0),
        "before": before,
        "after": after,
        "population": evidence.get("population"),
        "verified_rule_pairs": len(evidence["pairs"]),
        "alternate_spacing": "authentic_metrics_without_base_kerning",
        "measurement_units": "font_units",
        "legibility": None,
        "handwriting_fidelity": None,
    }


def check_shaping(data, rows, manifest):
    if manifest.script_code == "Deva":
        from .shaping import shaping_suite

        return shaping_suite(data, rows, manifest)
    raw = sfnt_bytes(data)
    f = TTFont(io.BytesIO(raw))
    font = hb.Font(hb.Face(raw))
    font.scale = (manifest.options.units_per_em, manifest.options.units_per_em)
    cmap = f.getBestCmap()
    checks = []
    for cp in sorted(cmap):
        ids, adv = shape(font, chr(cp))
        expected = f.getGlyphID(cmap[cp])
        if ids != [expected] or expected == 0 or adv != [f["hmtx"][cmap[cp]][0]]:
            raise ValueError("harfbuzz_mapping_failed")
        checks.append(dict(codepoint=cp, glyph_ids=ids, advances=adv))
    mixed = "Aa Zz 019 !?,."
    mixed = "".join(c for c in mixed if ord(c) in cmap)
    if not mixed:
        mixed = "".join(chr(cp) for cp in sorted(cmap)[:32])
    ids, adv = shape(font, mixed)
    if not ids or 0 in ids:
        raise ValueError("harfbuzz_mixed_failed")
    missing = next(cp for cp in [0x10FFFD, 0xFFFD, 0x378] if cp not in cmap)
    fallback, _ = shape(font, chr(missing))
    if fallback != [0]:
        raise ValueError("missing_fallback_failed")
    return {
        "status": "passed",
        "per_codepoint": checks,
        "calt_policy": "adjacent-repeat-v1",
        "measured_spacing": spacing_report(raw, rows, manifest),
        "contextual_cycling": validate_cycling(
            raw,
            {
                cp: [cmap[cp]] + [f"{cmap[cp]}.alt{i}" for i in range(1, len(values) + 1)]
                for cp, values in enabled_alternates(rows, manifest).items()
            },
        ),
        "mixed_glyph_ids": ids,
        "missing_codepoint": missing,
        "fallback_glyph_ids": fallback,
    }


def check_render(data, working, manifest):
    if manifest.script_code == "Deva":
        from .indic_render import render_smoke

        return render_smoke(data, working)
    raw = sfnt_bytes(data)
    f = TTFont(io.BytesIO(raw))
    font = ImageFont.truetype(io.BytesIO(raw), size=48)
    covered = [cp for cp in sorted(f.getBestCmap()) if cp != 32]
    for cp in covered:
        if font.getmask(chr(cp)).getbbox() is None:
            raise ValueError("blank_covered_glyph")
    missing = chr(next(cp for cp in [0x10FFFD, 0xFFFD, 0x378] if cp not in f.getBestCmap()))
    if font.getmask(missing).getbbox() is None:
        raise ValueError("blank_missing_fallback")
    image = Image.new("RGB", (960, 480), "white")
    draw = ImageDraw.Draw(image)
    for index in range(0, min(len(covered), 95), 19):
        text = "".join(chr(cp) for cp in covered[index : index + 19])
        draw.text((20, 10 + index // 19 * 85), text, font=font, fill="black")
    image.save(working / "render.png")
    return {
        "status": "passed",
        "covered_nonblank": len(covered),
        "notdef_used_for_covered": False,
        "fallback_visible": True,
        "render_sha256": hashlib.sha256((working / "render.png").read_bytes()).hexdigest(),
    }


def validate_font(data, manifest, rows, format, working: Path, *, layer_completed=None):
    working.mkdir(parents=True, exist_ok=True)
    path = working / ("font." + format)
    path.write_bytes(data)
    layers = {}
    functions = [
        lambda: check_structure(data, manifest, format, rows),
        lambda: check_ots(path, working),
        lambda: check_fontbakery(path, working),
        lambda: check_shaping(data, rows, manifest),
        lambda: check_render(data, working, manifest),
    ]
    for index, (name, run) in enumerate(zip(LAYERS, functions)):
        try:
            layers[name] = run()
        except Exception:
            # Third-party exceptions often contain names, paths or text. Persist a code.
            layers[name] = {"status": "failed", "error_code": name + "_validation_failed"}
        if layer_completed:
            layer_completed(index + 1)
    passed = all(x["status"] == "passed" for x in layers.values())
    report = coverage(
        rows,
        manifest,
        validated=passed,
        shaping=layers.get("harfbuzz") if passed and manifest.script_code == "Deva" else None,
    )
    warnings = report["warnings"] + layers["fontbakery"].get("warnings", [])
    return dict(
        schema_version=1,
        build_id=manifest.build_id,
        font_sha256="sha256:" + hashlib.sha256(data).hexdigest(),
        format=format,
        status=("passed_with_warnings" if warnings else "passed") if passed else "failed",
        coverage=report,
        layers=layers,
        warnings=warnings,
        tool_versions=manifest.tool_versions,
    )
