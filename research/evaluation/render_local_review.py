"""Render existing observations for review. No training, fitting or model inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from research.preparation.archive_audit import file_hash, safe_name
from research.preparation.check_study import check
from research.preparation.freeze import writer_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    check(args.staging, root / "research/configs/local-r1.json")
    rows = [json.loads(line) for line in (args.staging / "study-r1.jsonl").read_text().splitlines()]
    by_id = {r["id"]: r for r in rows}
    result = json.loads((args.run / "result.json").read_text())

    def paste(sheet, row, x, y, size):
        path = args.staging / "iiit-hw-dev-inner" / safe_name(row["path"])
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail(size)
            sheet.paste(image, (x, y))

    writers = sorted({r["writer"] for r in rows if r.get("style_role")})
    style = Image.new("RGB", (1450, 70 + 180 * len(writers)), "white")
    draw = ImageDraw.Draw(style)
    draw.text(
        (10, 10),
        "Geometry review: same calibration/query word sets in each writer row",
        fill="black",
    )
    draw.text(
        (10, 32),
        "Calibration and query use distinct words and pages; no generated text",
        fill="black",
    )
    for j, writer in enumerate(writers):
        draw.text((10, 75 + 180 * j), writer_id(writer), fill="black")
        for col, role in enumerate(("calibration", "query")):
            examples = sorted(
                [r for r in rows if r["writer"] == writer and r.get("style_role") == role],
                key=lambda r: r["label"],
            )[:4]
            draw.text((210 + col * 610, 58 + 180 * j), role, fill="black")
            for n, row in enumerate(examples):
                paste(
                    style,
                    row,
                    210 + col * 610 + (n % 2) * 300,
                    82 + 180 * j + (n // 2) * 65,
                    (285, 55),
                )
    style_path = args.run / "style-content-controlled-review.png"
    style.save(style_path)
    pairs = result["personal_baseline"]["pairs"]
    glyph = Image.new("RGB", (1350, 80 + 120 * len(pairs)), "white")
    draw = ImageDraw.Draw(glyph)
    draw.text(
        (10, 10),
        "Observed grapheme reuse: separate pages; no generated glyphs or font",
        fill="black",
    )
    for col, heading in enumerate(
        ("Held-out writer calibration", "Same writer query", "Different training writer")
    ):
        draw.text((10 + 450 * col, 40), heading, fill="black")
    for j, pair in enumerate(pairs):
        for col, key in enumerate(("calibration_id", "query_id", "generic_donor_id")):
            paste(glyph, by_id[pair[key]], 10 + 450 * col, 75 + 120 * j, (420, 75))
        draw.text(
            (10, 155 + 120 * j),
            f"{pair['writer']}  saved mask IoU: own {pair['same_writer_iou']:.3f}, generic {pair['generic_iou']:.3f}",
            fill="black",
        )
    glyph_path = args.run / "glyph-reuse-pairs-review.png"
    glyph.save(glyph_path)
    receipt = {
        "source_result_sha256": "sha256:" + file_hash(args.run / "result.json"),
        "rendered_files": {p.name: "sha256:" + file_hash(p) for p in (style_path, glyph_path)},
        "training": False,
        "model_inference": False,
        "metrics_recomputed": False,
        "blinded_human_fidelity_review": False,
    }
    (args.run / "visual-review-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
