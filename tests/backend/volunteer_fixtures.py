"""Synthetic fixtures for the private volunteer preprocessing and adaptation tests.

Everything is generated in the test's own temporary directory: five fake pages, 260 real
PNG crops with content addresses, a crop manifest, a saved prediction file and the pilot
receipt. No model runs, no real photographs and no network access.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from research.english.volunteer.manifest import (
    CROP_GENERATION_VERSION,
    EXPECTED_SAMPLES,
    LOWER,
    MANIFEST_VERSION,
    UPPER,
    WRITERS,
    expected_sample_id,
)

BASELINE_CORRECT = 132
BASELINE_ACCURACY = 0.5076923076923077
CROP_SIDE = 48
#: Correct letters per writer (letter index ranges), then case-only ranges.
CORRECT_RANGES = {0: (3, 28), 1: (3, 28), 2: (3, 28), 3: (3, 28), 4: (3, 30)}
CASE_ONLY_RANGES = {0: (34, 41), 1: (34, 41), 2: (34, 41), 3: (34, 41), 4: (34, 40)}


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def letter_crop(label: str, group: str) -> Image.Image:
    """One synthetic glyph: a dark stroke on white, shaped by the label."""
    image = Image.new("RGB", (CROP_SIDE, CROP_SIDE), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    width = 2 + (ord(label) % 5)
    top = 10 + (ord(label) % 7)
    bottom = CROP_SIDE - 10
    if group == "upper":
        draw.rectangle((12, top, 12 + width, bottom), fill=(28, 24, 40))
        draw.rectangle((12, top, 34, top + width), fill=(28, 24, 40))
    else:
        draw.line((14, top, 34, bottom), fill=(28, 24, 40), width=width)
    return image


def page_image(writer_index: int) -> Image.Image:
    image = Image.new("RGB", (200, 300), (240, 238, 232))
    draw = ImageDraw.Draw(image)
    draw.line((10, 150 + writer_index, 190, 150 + writer_index), fill=(200, 205, 215), width=2)
    return image


def wrong_prediction(truth: str) -> str:
    """A deterministic neighbour in the same case."""
    if truth in {"Z", "z"}:
        return truth.lower() if truth.isupper() else "a"
    return chr(ord(truth) + 1)


def _in(index: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= index <= bounds[1]


def prediction_for(writer_id: str, label: str) -> str:
    """A deterministic error pattern: 132/260 correct with 39 case-only errors."""
    writer_index = WRITERS.index(writer_id)
    letters = UPPER + LOWER
    index = letters.index(label)
    if _in(index, CORRECT_RANGES[writer_index]):
        return label
    if _in(index, CASE_ONLY_RANGES[writer_index]):
        return label.swapcase()
    return wrong_prediction(label)


def predictions_for(manifest_samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "version": "penforge-volunteer-predictions-v1",
            "sample_id": sample["sample_id"],
            "writer_id": sample["writer_id"],
            "truth": sample["truth"],
            "raw_prediction": prediction_for(sample["writer_id"], sample["truth"]),
        }
        for sample in manifest_samples
    ]


def build_pilot_run(root: Path) -> dict[str, Any]:
    """Write a complete synthetic five-writer pilot run and return its handles."""
    root.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for index, writer in enumerate(WRITERS):
        page_bytes = _png(page_image(index))
        page_digest = hashlib.sha256(page_bytes).hexdigest()
        (root / "sources").mkdir(exist_ok=True)
        (root / f"sources/{writer}.png").write_bytes(page_bytes)
        sources.append(
            {
                "writer_id": writer,
                "source_path": f"/private/volunteer/{writer}.png",
                "source_sha256": page_digest,
                "source_bytes": len(page_bytes),
                "copied_path": f"sources/{writer}.png",
                "page_width": 200,
                "page_height": 300,
            }
        )
        for group, letters in (("upper", UPPER), ("lower", LOWER)):
            for label in letters:
                payload = _png(letter_crop(label, group))
                digest = hashlib.sha256(payload).hexdigest()
                (root / "crops/images").mkdir(parents=True, exist_ok=True)
                (root / f"crops/images/{digest}.png").write_bytes(payload)
                samples.append(
                    {
                        "sample_id": expected_sample_id(writer, group, label),
                        "writer_id": writer,
                        "group": group,
                        "truth": label,
                        "letter_box": [12, 10, 34, 38],
                        "crop_box": [10, 8, 36, 40],
                        "crop_width": CROP_SIDE,
                        "crop_height": CROP_SIDE,
                        "crop_sha256": digest,
                        "crop_path": f"crops/images/{digest}.png",
                        "source_sha256": page_digest,
                        "crop_generation_version": CROP_GENERATION_VERSION,
                        "review_status": "reviewed_agent",
                    }
                )
    assert len(samples) == EXPECTED_SAMPLES
    manifest = {
        "version": MANIFEST_VERSION,
        "pilot": "private_five_writer_exploratory_pilot",
        "scope": "isolated_uppercase_and_lowercase_letter_crops_only",
        "crop_generation": {
            "version": CROP_GENERATION_VERSION,
            "margin_fraction": 0.04,
            "square_padding": "centered_white_rgb",
            "output_side": 384,
            "resampling": "lanczos",
            "contrast_adjustment": "none_original_page_pixels",
            "line_erasure": "none",
        },
        "sources": sources,
        "included_samples": len(samples),
        "excluded": [],
        "samples": samples,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    (root / "crop-manifest.json").write_bytes(manifest_bytes)
    records = predictions_for(samples)
    correct = sum(record["truth"] == record["raw_prediction"] for record in records)
    assert correct == BASELINE_CORRECT, correct
    predictions_bytes = "".join(
        json.dumps(record, sort_keys=True) + "\n" for record in records
    ).encode()
    (root / "predictions-private.jsonl").write_bytes(predictions_bytes)
    (root / "report.json").write_text(
        json.dumps(
            {
                "version": "penforge-volunteer-report-v1",
                "status": "private_five_writer_exploratory_pilot",
                "samples": {
                    "total": len(records),
                    "correct": correct,
                    "accuracy": BASELINE_ACCURACY,
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    (root / "prepared.json").write_text(
        json.dumps(
            {
                "status": "prepared_not_evaluated",
                "crop_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "included_samples": len(samples),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return {
        "run": root,
        "samples": samples,
        "records": records,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "predictions_sha256": hashlib.sha256(predictions_bytes).hexdigest(),
        "baseline": {record["sample_id"]: record["raw_prediction"] for record in records},
    }
