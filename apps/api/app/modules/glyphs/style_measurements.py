"""Bounded deterministic geometry descriptors; no learned model or inferred capture data."""

import math
import statistics
from typing import TypedDict

from penforge_contracts import canonical_bytes, content_hash

ALGORITHM = "outline-descriptors-v1"


class MeasurementConfig(TypedDict):
    curve_steps: int
    scanlines: int
    precision: int
    fill: str


CONFIG: MeasurementConfig = {"curve_steps": 16, "scanlines": 32, "precision": 6, "fill": "even_odd"}


def rounded(value):
    return round(float(value), CONFIG["precision"]) if value is not None else None


def distribution(values):
    if not values:
        return None
    values = sorted(values)
    return {
        "n": len(values),
        "min": rounded(values[0]),
        "median": rounded(statistics.median(values)),
        "max": rounded(values[-1]),
    }


def polygons(outline):
    paths = []
    for contour in outline["contours"]:
        start = contour["start"]
        path = [(start["x"], start["y"])]
        for segment in contour["segments"]:
            end = segment["to"]
            controls = (
                [start]
                + (
                    [segment["c"]]
                    if segment["type"] == "quadratic"
                    else [segment["c1"], segment["c2"]]
                    if segment["type"] == "cubic"
                    else []
                )
                + [end]
            )
            degree = len(controls) - 1
            steps = 1 if degree == 1 else CONFIG["curve_steps"]
            for i in range(1, steps + 1):
                t = i / steps
                path.append(
                    tuple(
                        sum(
                            math.comb(degree, k) * (1 - t) ** (degree - k) * t**k * p[axis]
                            for k, p in enumerate(controls)
                        )
                        for axis in ("x", "y")
                    )
                )
            start = end
        paths.append(path)
    return paths


def describe(outline):
    paths = polygons(outline)
    points = [p for path in paths for p in path]
    if not points:
        return {
            "width": 0,
            "top": None,
            "bottom": None,
            "slant_proxy_degrees": None,
            "ink_chord_widths": None,
            "advance": outline["metrics"]["advance_width"],
            "left_bearing": outline["metrics"]["left_side_bearing"],
            "right_bearing": None,
        }
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
    chords: list[float] = []
    centers: list[tuple[float, float]] = []
    for i in range(CONFIG["scanlines"]):
        y = y0 + (y1 - y0) * (i + 0.5) / CONFIG["scanlines"]
        crossings = sorted(
            a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            for path in paths
            for a, b in zip(path, path[1:] + path[:1])
            if (a[1] <= y < b[1]) or (b[1] <= y < a[1])
        )
        spans = [(a, b) for a, b in zip(crossings[::2], crossings[1::2]) if b > a]
        width = sum(b - a for a, b in spans)
        chords.extend(b - a for a, b in spans)
        if width:
            centers.append((y, sum((a + b) / 2 * (b - a) for a, b in spans) / width))
    slant = None
    if len(centers) > 1:
        ym, xm = (statistics.mean(p[i] for p in centers) for i in (0, 1))
        den = sum((y - ym) ** 2 for y, _ in centers)
        if den:
            slant = math.degrees(math.atan(sum((y - ym) * (x - xm) for y, x in centers) / den))
    advance = outline["metrics"]["advance_width"]
    return {
        "width": rounded(x1 - x0),
        "top": rounded(y1),
        "bottom": rounded(y0),
        "slant_proxy_degrees": rounded(slant),
        "ink_chord_widths": distribution(chords),
        "advance": advance,
        "left_bearing": outline["metrics"]["left_side_bearing"],
        "right_bearing": rounded(advance - outline["metrics"]["left_side_bearing"] - (x1 - x0)),
    }


def measure(snapshot_hash, references):
    entries = []
    for reference in sorted(
        references, key=lambda r: (r["codepoints"], r["variant_index"], r["revision_hash"])
    ):
        entries.append(
            {k: v for k, v in reference.items() if k not in {"outline", "capture_observation"}}
            | {"geometry": describe(reference["outline"])}
        )
        if reference.get("capture_observation"):
            observation = reference["capture_observation"]
            entries[-1]["capture_observation"] = {
                "source_hash": observation["source_hash"],
                **{
                    k: distribution(observation[k])
                    for k in ("stroke_durations_ms", "interstroke_gaps_ms", "observed_pressure")
                },
            }

    def tops(characters):
        return [
            e["geometry"]["top"]
            for e in entries
            if len(e["codepoints"]) == 1
            and chr(e["codepoints"][0]) in characters
            and e["geometry"]["top"] is not None
        ]

    cap = distribution(tops("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    xheight = distribution(tops("acemnorsuvwxz"))
    cap_median = cap["median"] if cap else None
    captures = {
        r["capture_observation"]["source_hash"]: r["capture_observation"]
        for r in references
        if r.get("capture_observation")
    }
    durations = [
        v for observation in captures.values() for v in observation.get("stroke_durations_ms", [])
    ]
    gaps = [
        v for observation in captures.values() for v in observation.get("interstroke_gaps_ms", [])
    ]
    pressures = [
        v for observation in captures.values() for v in observation.get("observed_pressure", [])
    ]
    body = {
        "schema_version": 1,
        "source_snapshot_hash": snapshot_hash,
        "algorithm_version": ALGORITHM,
        "configuration": CONFIG,
        "references": entries,
        "summary": {
            "cap_height": cap,
            "x_height": xheight,
            "x_to_cap_ratio": rounded(xheight["median"] / cap_median)
            if xheight and cap_median
            else None,
            "ascender_top": distribution(tops("bdfhklt")),
            "descender_bottom": distribution(
                [
                    e["geometry"]["bottom"]
                    for e in entries
                    if e["codepoints"]
                    and chr(e["codepoints"][0]) in "gjpqy"
                    and e["geometry"]["bottom"] is not None
                ]
            ),
            "slant_proxy_degrees": distribution(
                [
                    e["geometry"]["slant_proxy_degrees"]
                    for e in entries
                    if e["geometry"]["slant_proxy_degrees"] is not None
                ]
            ),
            "baseline_variation": None,
            "writing_rhythm": {
                "stroke_duration_ms": distribution(durations),
                "interstroke_gap_ms": distribution(gaps),
                "scope": "within_isolated_captures",
            }
            if durations
            else None,
            "observed_interglyph_spacing": None,
            "pressure": distribution(pressures),
            "learned_embedding": None,
        },
        "limitations": [
            "Slant is an ink-centroid regression, not a pen-angle estimate.",
            "Ink chords measure horizontal filled runs, not physical pen width.",
            "Normalized isolated glyphs do not observe line baselines, spacing or text-writing rhythm; observed stroke intervals remain separate.",
            "Curve measurements use 16 uniform steps; font-unit descriptors are not handwriting-quality evidence.",
        ],
    }
    return body, content_hash(body), canonical_bytes(body)
