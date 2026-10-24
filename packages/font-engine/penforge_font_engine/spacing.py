"""Measured letter spacing from approved outlines.

The input is the writer's own approved base glyphs, so measurement is taken from
authentic geometry rather than from a reference font. This module makes no
handwriting-quality claim: it measures ink gaps in font units, applies a bounded
correction, and reports what it measured.

Policy (``penforge-spacing-v2``):
    * only approved ``cmap`` base glyphs with real ink take part;
    * the target gap is the median natural gap over every ordered pair of those
      glyphs, so one unusually tight or loose writer habit cannot set it;
    * a pair is corrected only when its gap differs from that median by more
      than ``TOLERANCE`` of the em, and the shift is clamped to ``AMPLITUDE`` of
      the em scaled by the 0-100 control;
    * a correction may never produce a non-positive advance or eat more than
      half of the glyph's own advance;
    * at most ``MAX_RULES`` pairs are emitted, chosen by largest absolute
      correction then codepoint order, so output is independent of row order.

Spacing is Latin-only. Devanagari positioning belongs to the Phase 09
``dev2``/``DFLT`` feature generation and is left untouched.
"""

from __future__ import annotations

import io
import statistics
from collections.abc import Mapping, Sequence

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont

POLICY = "penforge-spacing-v2"
TOLERANCE = 0.02  # em fraction; smaller deviations are left alone
AMPLITUDE = 0.02  # em fraction at spacing == 100
MAX_RULES = 4096  # ≈48 KiB of GPOS pair data; a documented, deterministic ceiling
MAX_CONTROL = 100

# Representative runs: a long sentence, repeated letters, tight/loose pairs,
# punctuation, digits and a word boundary. Mixed-script runs are covered by the
# Phase 09 shaping suite and are deliberately absent here.
RUNS = (
    "the quick brown fox jumps over the lazy dog",
    "yellow yellow yellow",
    "To Ava, WAVE Yo",
    "alignment rhythm",
    "eee lll ooo",
    "a.b,c!d?e 2026",
    "A T",
    "AVATAR TOTAL",
)


def ink_extents(outline: Mapping) -> tuple[float, float] | None:
    """Horizontal ink extent of one outline, or None when it has no contours."""
    if not outline["contours"]:
        return None
    pen = BoundsPen(None)
    for contour in outline["contours"]:
        start = contour["start"]
        pen.moveTo((start["x"], start["y"]))
        for segment in contour["segments"]:
            kind = segment["type"]
            if kind == "line":
                pen.lineTo((segment["to"]["x"], segment["to"]["y"]))
            elif kind == "quadratic":
                pen.qCurveTo(
                    (segment["c"]["x"], segment["c"]["y"]),
                    (segment["to"]["x"], segment["to"]["y"]),
                )
            else:
                pen.curveTo(
                    (segment["c1"]["x"], segment["c1"]["y"]),
                    (segment["c2"]["x"], segment["c2"]["y"]),
                    (segment["to"]["x"], segment["to"]["y"]),
                )
        pen.closePath()
    if pen.bounds is None:
        return None
    return pen.bounds[0], pen.bounds[2]


def _glyph_metrics(outline: Mapping) -> tuple[float, float, float]:
    advance = outline["metrics"]["advance_width"]
    extents = ink_extents(outline)
    if extents is None:
        raise ValueError("glyph_has_no_ink")
    return advance, extents[0], extents[1]


def amplitude_units(manifest) -> int:
    """Maximum shift in font units for this build's spacing control."""
    control = manifest.options.spacing
    if not control:
        return 0
    return max(0, round(manifest.options.units_per_em * AMPLITUDE * control / MAX_CONTROL))


def natural_gap(
    previous: tuple[float, float, float], following: tuple[float, float, float]
) -> float:
    """Ink gap between two glyphs placed adjacent at their default advances."""
    advance, _, right = previous
    _, left, _ = following
    return advance - right + left


def approved_in_ink_order(approved: Mapping[int, tuple[Mapping, str]]) -> list[tuple[int, tuple]]:
    return [
        (cp, _glyph_metrics(outline))
        for cp, (outline, _) in sorted(approved.items())
        if cp != 32 and outline["contours"]
    ]


def measure(approved: Mapping[int, tuple[Mapping, str]], manifest) -> dict:
    """Return the bounded correction per ordered pair plus the evidence behind it."""
    limit = amplitude_units(manifest)
    glyphs = approved_in_ink_order(approved)
    evidence: dict = {
        "policy": POLICY,
        "control": manifest.options.spacing,
        "amplitude_units": limit,
        "tolerance_units": round(manifest.options.units_per_em * TOLERANCE),
        "pairs_considered": 0,
        "pairs": {},
        "status": "disabled",
    }
    if limit <= 0 or len(glyphs) < 2:
        return evidence

    gaps = {(a, b): natural_gap(first, second) for a, first in glyphs for b, second in glyphs}
    if not gaps:
        return evidence
    target = statistics.median(gaps.values())
    tolerance = manifest.options.units_per_em * TOLERANCE
    candidates = []
    for (a, b), gap in gaps.items():
        delta = target - gap
        if abs(delta) <= tolerance:
            continue
        shift = max(-limit, min(limit, round(delta)))
        if gap >= 0 and gap + shift < 0:
            continue  # Do not introduce an ink collision in an originally separated pair.
        advance = approved[a][0]["metrics"]["advance_width"]
        if shift == 0 or advance + shift <= 0 or shift < -0.5 * advance:
            continue
        candidates.append((a, b, shift))
    candidates.sort(key=lambda item: (-abs(item[2]), item[0], item[1]))
    selected = candidates[:MAX_RULES]
    pair_map = {(a, b): shift for a, b, shift in sorted(selected)}
    after = [gap + pair_map.get((a, b), 0) for (a, b), gap in gaps.items()]
    before = list(gaps.values())
    evidence.update(
        status="applied" if selected else "no_pairs_beyond_tolerance",
        pairs_considered=len(gaps),
        target_gap=round(target, 3),
        pairs=pair_map,
        corrected_pairs=len(selected),
        uncorrected_outliers=max(0, len(candidates) - len(selected)),
        max_abs_shift=max((abs(shift) for _, _, shift in selected), default=0),
        population={
            "before": _distribution(before),
            "after": _distribution(after),
            "scope": "every_ordered_pair_of_approved_inked_glyphs",
        },
    )
    return evidence


def _distribution(values: Sequence[float]) -> dict:
    longest, shortest = max(values), min(values)
    return {
        "samples": len(values),
        "median": round(statistics.median(values), 3),
        "stdev": round(statistics.pstdev(values), 3),
        "min": round(shortest, 3),
        "max": round(longest, 3),
    }


def kerning_source(pairs: Mapping[tuple[int, int], int], names: Mapping[int, str]) -> str:
    """Feature source compiled together with the contextual substitutions."""
    if not pairs:
        return ""
    lines = ["feature kern {"]
    for (a, b), shift in pairs.items():
        lines.append(f"    pos {names[a]} {names[b]} {shift};")
    lines += ["} kern;"]
    return "\n".join(lines)


def shape_run(
    data: bytes, text: str, *, kern: bool, calt: bool = False
) -> tuple[list[int], list[int]]:
    """Glyph ids and advances of one shaped run, with kerning on or off."""
    font = hb.Font(hb.Face(data))
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.direction, buffer.script, buffer.language = "ltr", "Latn", "en"
    hb.shape(font, buffer, {"kern": kern, "liga": False, "calt": calt})
    return (
        [info.codepoint for info in buffer.glyph_infos],
        [position.x_advance for position in buffer.glyph_positions],
    )


def shape_advances(data: bytes, text: str, *, kern: bool) -> list[int]:
    """Advance of each shaped glyph for one run, with kerning on or off."""
    return shape_run(data, text, kern=kern)[1]


def run_gaps(data: bytes, runs: Sequence[str], *, kern: bool, calt: bool = False) -> list[float]:
    """Ink gap at every adjacent pair inside each run, in font units.

    Advances come from the shaped buffer, so the measurement reflects what a
    renderer will actually lay out rather than the font's static metrics.
    """
    ttf = TTFont(io.BytesIO(data))
    glyphs = ttf.getGlyphSet()
    name_for = {ttf.getGlyphID(name): name for name in ttf.getGlyphOrder()}
    bounds = {}
    gaps: list[float] = []
    for text in runs:
        ids, advances = shape_run(data, text, kern=kern, calt=calt)
        names = [name_for[glyph] for glyph in ids]
        for name in names:
            if name not in bounds and name != ".notdef":
                pen = BoundsPen(glyphs)
                glyphs[name].draw(pen)
                bounds[name] = pen.bounds
        for index in range(len(names) - 1):
            left, right = bounds.get(names[index]), bounds.get(names[index + 1])
            if left is None or right is None:
                continue
            gaps.append(advances[index] - left[2] + right[0])
    return gaps


def verify_shifts(
    data: bytes,
    runs: Sequence[str],
    pairs: Mapping[tuple[int, int], int],
    *,
    tolerance: float = 2.0,
    calt: bool = False,
) -> list[dict]:
    """Check that every shaped pair receives exactly its intended shift.

    A mismatch means the compiled geometry no longer supports the measurement
    that produced the rule, so the build is rejected rather than shipped.
    """
    mismatches = []
    font = TTFont(io.BytesIO(data))
    base_codepoints = {font.getGlyphID(name): cp for cp, name in font.getBestCmap().items()}
    for text in runs:
        ids, off = shape_run(data, text, kern=False, calt=calt)
        on_ids, on = shape_run(data, text, kern=True, calt=calt)
        if ids != on_ids or len(on) != len(off) or any(a <= 0 for a in on):
            raise ValueError("spacing_changed_mapping_or_invalid_advance")
        for index in range(min(len(off), len(on)) - 1):
            # Position only measured base forms; substituted authentic forms retain
            # their own spacing. No base-derived rule is invented for an alternate.
            left = base_codepoints.get(ids[index])
            right = base_codepoints.get(ids[index + 1])
            intended = pairs.get((left, right), 0) if left is not None and right is not None else 0
            actual = on[index] - off[index]
            if abs(actual - intended) > tolerance:
                mismatches.append(
                    dict(
                        pair=[ord(text[index]), ord(text[index + 1])],
                        intended=intended,
                        actual=round(actual, 3),
                    )
                )
    return mismatches


def summarize(gaps: Sequence[float], *, units_per_em: int = 1000) -> dict:
    """Separate, non-composite spacing measurements.

    A gap is a collision when ink overlaps and a disconnection when it exceeds
    12% of the em. Both are reported as counts, never folded into one score.
    """
    if not gaps:
        return {"samples": 0}
    ordered = sorted(gaps)
    return {
        "samples": len(gaps),
        "median": round(statistics.median(gaps), 3),
        "stdev": round(statistics.pstdev(gaps), 3),
        "min": round(ordered[0], 3),
        "p05": round(ordered[int(0.05 * (len(ordered) - 1))], 3),
        "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 3),
        "max": round(ordered[-1], 3),
        "collisions": sum(1 for gap in gaps if gap < 0),
        "disconnections": sum(1 for gap in gaps if gap > 0.12 * units_per_em),
    }
