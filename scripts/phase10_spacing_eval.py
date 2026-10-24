"""Phase 10 spacing evaluation: build, measure, render and record artifacts.

Runs the measured-spacing feature over authored synthetic control geometry at
three control settings, writes the fonts, the shaping reports and rendered
specimens, and prints one table. It never claims handwriting quality: the
inputs are deterministic synthetic outlines, so the numbers describe font
engineering, not the writer's hand.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from penforge_font_engine.compiler import compile_font, coverage
from penforge_font_engine.spacing import RUNS as VALIDATION_RUNS
from penforge_font_engine.validation import check_shaping, validate_font
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/backend"))

from font_fixtures import fixture_glyphs, fixture_manifest  # noqa: E402

OUT = ROOT / "artifacts/phase10"
RUNS = (
    "the quick brown fox jumps over the lazy dog",
    "yellow yellow yellow",
    "To Ava, WAVE Yo",
    "a.b,c!d?e 2026",
)


def varied():
    """Deterministic per-glyph variation: spread without contour overlap."""
    rows = fixture_glyphs()
    for row in rows:
        cp = row["outline"]["codepoints"][0]
        if cp == 32:
            continue
        shift = (cp % 7 - 3) * 2
        for contour in row["outline"]["contours"]:
            contour["start"]["x"] += shift
            for segment in contour["segments"]:
                for key in ("to", "c", "c1", "c2"):
                    if key in segment:
                        segment[key]["x"] += shift
        row["outline"]["metrics"]["advance_width"] += (cp % 5) * 8
    return rows


def render(data: bytes, path: Path) -> None:
    font = ImageFont.truetype(io.BytesIO(data), size=34)
    image = Image.new("RGB", (1100, 190), "#f8f1e3")
    draw = ImageDraw.Draw(image)
    draw.text((24, 42), "To Ava, WAVE Yo", font=font, fill="#192e35")
    draw.text((24, 106), "the quick brown fox", font=font, fill="#192e35")
    image.save(path)


def main() -> int:
    rows = varied()
    summary = []
    for control in (0, 50, 100):
        manifest = fixture_manifest(options={"spacing": control} if control else {})
        folder = OUT / f"spacing-{control:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        data = compile_font(manifest, rows, "ttf")
        (folder / "font.ttf").write_bytes(data)
        report = check_shaping(data, rows, manifest)
        (folder / "shaping.json").write_text(json.dumps(report, indent=2))
        (folder / "coverage.json").write_text(json.dumps(coverage(rows, manifest), indent=2))
        render(data, folder / "specimen.png")
        if control == 100:
            validation = validate_font(data, manifest, rows, "ttf", OUT / "validation")
            (OUT / "validation/report.json").write_text(json.dumps(validation, indent=2) + "\n")
            if validation["status"] not in {"passed", "passed_with_warnings"}:
                raise ValueError("spacing_evaluation_validation_failed")
        spacing = report["measured_spacing"]
        summary.append(
            {
                "control": control,
                "status": spacing["status"],
                "corrected_pairs": spacing["corrected_pairs"],
                "amplitude_units": spacing["amplitude_units"],
                "max_abs_shift": spacing["max_abs_shift"],
                "population": spacing["population"],
                "runs": {"before": spacing["before"], "after": spacing["after"]},
                "font_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "gpos": "GPOS" in TTFont(io.BytesIO(data)),
            }
        )
    (OUT / "summary.json").write_text(
        json.dumps(
            {
                "policy": "penforge-spacing-v2",
                "evidence_domain": "synthetic_control",
                "handwriting_fidelity": None,
                "legibility": None,
                "runs": list(VALIDATION_RUNS),
                "specimen_runs": list(RUNS),
                "variants": summary,
            },
            indent=2,
        )
    )
    print(f"{'control':>7} {'pairs':>6} {'amp':>4} {'max':>4} {'pop stdev before→after':>24}")
    for row in summary:
        population = row["population"]
        if population is None:
            spread = "disabled"
        else:
            spread = f"{population['before']['stdev']:>8} → {population['after']['stdev']:<8}"
        print(
            f"{row['control']:>7} {row['corrected_pairs']:>6} {row['amplitude_units']:>4} "
            f"{row['max_abs_shift']:>4} {spread}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
