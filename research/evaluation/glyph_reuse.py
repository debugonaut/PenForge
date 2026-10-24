"""Direct non-ML personal baseline on observed isolated grapheme word crops.

This tests reuse/selection, NOT unseen-character synthesis or a validated font.
All calibration/query writers are held out from global recognition training.
"""

from __future__ import annotations

from collections import defaultdict

import cv2
import numpy as np
from penforge_contracts.unicode import graphemes
from PIL import Image, ImageDraw

from research.preparation.archive_audit import sha
from research.preparation.freeze import writer_id


def clusters(text):
    return ["".join(map(chr, group)) for group in graphemes(text)]


def ink_crop(path):
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("missing_verified_glyph_source")
    _, mask = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    yy, xx = np.where(mask > 0)
    if not len(xx):
        raise ValueError("empty_glyph_ink")
    return mask[yy.min() : yy.max() + 1, xx.min() : xx.max() + 1]


def fit_frame(mask, width=64, height=64):
    ratio = min((width - 4) / mask.shape[1], (height - 4) / mask.shape[0])
    w, h = max(1, round(mask.shape[1] * ratio)), max(1, round(mask.shape[0] * ratio))
    resized = cv2.resize(mask, (w, h), interpolation=cv2.INTER_AREA) >= 128
    result = np.zeros((height, width), bool)
    y, x = (height - h) // 2, (width - w) // 2
    result[y : y + h, x : x + w] = resized
    return result


def iou(a, b):
    union = np.count_nonzero(a | b)
    return float(np.count_nonzero(a & b) / union) if union else None


def assemble(masks):
    # Explicitly arbitrary research layout; baseline, spacing and joins are NOT observed.
    tiles = []
    for mask in masks:
        ratio = 48 / mask.shape[0]
        tiles.append(cv2.resize(mask, (max(1, round(mask.shape[1] * ratio)), 48)))
        tiles.append(np.zeros((48, 4), np.uint8))
    return np.concatenate(tiles[:-1], axis=1)


def evaluate(rows, staging, output, config):
    training, test = defaultdict(list), defaultdict(list)
    for i, row in enumerate(rows):
        if not row.get("glyph_candidate"):
            continue
        if row["split"] == "train":
            training[row["label"]].append(i)
        if row["split"] == "test":
            test[row["writer"], row["label"]].append(i)
    cache = {}

    def crop(i):
        if i not in cache:
            cache[i] = ink_crop(staging / config["corpus"] / rows[i]["path"])
        return cache[i]

    def ordered(indices):
        return sorted(
            indices, key=lambda i: sha(f"glyph:{config['seed']}:{rows[i]['id']}".encode())
        )

    calibration: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    pairs = []
    for (writer, label), indices in sorted(test.items()):
        indices = ordered(indices)
        a = indices[0]
        others = [i for i in indices[1:] if rows[i]["page_id"] != rows[a]["page_id"]]
        if not others or not training.get(label):
            continue
        q, donor = others[0], ordered(training[label])[0]
        cal_mask, query_mask, generic = (
            fit_frame(crop(a)),
            fit_frame(crop(q)),
            fit_frame(crop(donor)),
        )
        matched = max(training[label], key=lambda i: iou(fit_frame(crop(i)), cal_mask) or 0)
        pairs.append(
            {
                "writer": writer_id(writer),
                "calibration_id": rows[a]["id"],
                "query_id": rows[q]["id"],
                "generic_donor_id": rows[donor]["id"],
                "same_writer_iou": iou(cal_mask, query_mask),
                "generic_iou": iou(generic, query_mask),
                "calibration_matched_generic_iou": iou(fit_frame(crop(matched)), query_mask),
                "label_hash": sha(label.encode()),
            }
        )
        calibration[writer][label] = (a, donor)
    novel = []
    for i, row in enumerate(rows):
        if row["split"] != "test":
            continue
        pieces = clusters(row["label"])
        refs = calibration.get(row["writer"], {})
        if len(pieces) < 2 or not all(g in refs for g in pieces):
            continue
        if any(rows[refs[g][0]]["page_id"] == row["page_id"] for g in pieces):
            continue
        own = assemble([crop(refs[g][0]) for g in pieces])
        generic = assemble([crop(refs[g][1]) for g in pieces])
        truth = fit_frame(crop(i), 256, 64)
        novel.append(
            {
                "id": row["id"],
                "writer": writer_id(row["writer"]),
                "own_bitmap_iou": iou(fit_frame(own, 256, 64), truth),
                "generic_bitmap_iou": iou(fit_frame(generic, 256, 64), truth),
                "source_index": i,
            }
        )

    def means(values):
        return {
            key: float(np.mean([r[key] for r in values])) if values else None
            for key in ("same_writer_iou", "generic_iou", "calibration_matched_generic_iou")
        }

    report = {
        "status": "measured_selection_only" if pairs else "insufficient_repeated_graphemes",
        "heldout_writers": len(calibration),
        "paired_graphemes": len(pairs),
        "geometry_comparison": "centered_aspect_preserving_64px_otsu_mask_iou",
        "means": means(pairs),
        "by_writer": {
            w: {
                "n": sum(r["writer"] == w for r in pairs),
                **means([r for r in pairs if r["writer"] == w]),
            }
            for w in sorted({r["writer"] for r in pairs})
        },
        "novel_word_bitmap_assembly": {
            "n": len(novel),
            "same_writer_iou": float(np.mean([r["own_bitmap_iou"] for r in novel]))
            if novel
            else None,
            "generic_iou": float(np.mean([r["generic_bitmap_iou"] for r in novel]))
            if novel
            else None,
            "calibration_text_disjoint": True,
            "query_pages_disjoint_from_calibration": True,
            "font_shaping_or_export_validated": False,
        },
        "new_glyph_shapes_generated": 0,
        "font_files_created": 0,
        "pairs": pairs,
        "human_fidelity": None,
        "limitations": [
            "Single-grapheme word labels supply whole-crop boundaries only; scalar/component boundaries are absent.",
            "Same-writer reuse is a personal calibration baseline, not global learned generation.",
            "Masks are centered/resized for a mechanical comparison, not canonical font outlines.",
            "Bitmap strips use arbitrary 48px height and 4px gaps; baseline, spacing, anchors, joins and shaping are unvalidated.",
            "No owner-approved snapshot, TTF/OTF/WOFF2, human resemblance or usable personal font follows from this experiment.",
        ],
    }
    chosen = []
    for writer in sorted({r["writer"] for r in novel}):
        chosen.extend([r for r in novel if r["writer"] == writer][:4])
    if chosen:
        sheet = Image.new("RGB", (1440, 70 + 125 * len(chosen)), "white")
        draw = ImageDraw.Draw(sheet)
        for x, title in (
            (10, "Held-out word"),
            (490, "Own observed graphemes: bitmap strip"),
            (970, "Training-writer graphemes: bitmap strip"),
        ):
            draw.text((x, 12), title, fill="black")
        draw.text(
            (10, 36),
            "Research selection baseline; no font, generated glyphs or validated joining",
            fill="black",
        )
        for j, case in enumerate(chosen):
            i = case["source_index"]
            refs = calibration[rows[i]["writer"]]
            pieces = clusters(rows[i]["label"])
            images = [
                crop(i),
                assemble([crop(refs[g][0]) for g in pieces]),
                assemble([crop(refs[g][1]) for g in pieces]),
            ]
            for col, mask in enumerate(images):
                image = Image.fromarray(255 - mask).convert("RGB")
                image.thumbnail((450, 80))
                sheet.paste(image, (10 + col * 480, 65 + j * 125))
            draw.text((10, 150 + j * 125), case["writer"], fill="black")
        sheet.save(output / "glyph-reuse-novel-words.png")
    return report
