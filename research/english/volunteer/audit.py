"""Data-only error and crop audit of the completed five-writer volunteer pilot.

This module inspects the frozen pilot without running the model: it re-reads the crop
manifest, the saved predictions and the crop pixels, measures each crop mechanically and
scores every error against a **fixed, versioned reviewer table**. It writes private
error-review contact sheets and one aggregate audit report, and it never touches the
pilot's crops, manifest, predictions or report.

Three kinds of evidence are kept strictly separate and labelled as such:

* **mechanical measurements** — brightness, contrast, ink fraction, ink bounding box,
  margin fractions, edge contact, horizontal-ruled-line strength, and reviewed-box
  geometry (overlap, width, gap) computed from pixels and metadata only;
* **model-based signals** — the saved pilot predictions, which suggest a shifted label
  when consecutive samples in a row are predicted as their neighbours;
* **reviewer judgment** — :data:`REVIEW_TABLE`, a fixed classification written after
  visually inspecting every crop at full resolution. It is an author's reading of the
  images, not independent human adjudication, and it never changes a label or a crop.

Categories are deliberately narrow. Only :data:`CROP_DEFECT_CATEGORIES` count as a crop
defect in the aggregate; everything else is a recognition outcome.
"""

from __future__ import annotations

import hashlib  # noqa: F401  (kept for provenance helpers used by the owner stages)
import io
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from research.english.volunteer.manifest import (
    EXPECTED_SAMPLES,
    LOWER,
    UPPER,
    WRITERS,
    Manifest,
    ManifestSample,
    load_manifest,
)
from research.english.volunteer.prepare import (
    MANIFEST_NAME,
    PREDICTIONS_NAME,
    REPORT_NAME,
    private_run_directory,
)
from research.pretrained.artifacts import digest_file

AUDIT_VERSION = "penforge-volunteer-crop-audit-v1"
REVIEW_VERSION = "penforge-volunteer-error-review-v1"
REVIEW_DIR = "error-review"
AUDIT_NAME = "crop-audit.json"

#: A crop whose ink is not the labelled letter, or is only a fragment of another one.
LABEL_SHIFT = "label_shift"
CLIPPED_GLYPH = "clipped_glyph"
FRAGMENT_OR_EMPTY = "fragment_or_empty"
#: The labelled glyph is present but a capture artefact crosses it.
LINE_ARTEFACT = "line_artefact"
NEIGHBOUR_INTRUSION = "neighbour_intrusion"
LOW_CONTRAST = "low_contrast"
#: Crop and capture are fine; the written form is plausibly readable as the prediction.
AMBIGUOUS_FORM = "ambiguous_form"
#: Crop and capture are fine and the glyph is clear; the model was simply wrong.
RECOGNITION_ERROR = "recognition_error"
#: Scored exactly right and no defect was recorded.
RECOGNIZED = "recognized"

CATEGORIES = (
    LABEL_SHIFT,
    CLIPPED_GLYPH,
    FRAGMENT_OR_EMPTY,
    LINE_ARTEFACT,
    NEIGHBOUR_INTRUSION,
    LOW_CONTRAST,
    AMBIGUOUS_FORM,
    RECOGNITION_ERROR,
)
#: Every category a crop can carry, including the clean ones.
ALL_CATEGORIES = (RECOGNIZED, *CATEGORIES)
CROP_DEFECT_CATEGORIES = frozenset({LABEL_SHIFT, CLIPPED_GLYPH, FRAGMENT_OR_EMPTY})

#: Fixed reviewer table. Only non-default categories are listed; anything else is
#: ``recognition_error`` because the crop and capture were judged usable.
#:
#: The recurring mechanism is a duplicated reviewed box: one glyph received two boxes,
#: so every later label in that writing row names the previous glyph until a second
#: duplicate absorbs the shift. The overlapping box pairs (6 px in every case) are
#: recorded by :func:`box_geometry`, independently of the predictions.
REVIEW_TABLE: dict[str, tuple[str, str]] = {
    # W01 — duplicate box pair on the capital I shifts J..S; the row's final S is
    # unboxed. Lowercase has duplicate pairs at g/h and n/o, so labels from h onward
    # name the previous glyph, twice over from p onward.
    **{
        f"W01-upper-{letter}": (
            LABEL_SHIFT,
            "duplicate reviewed box on I; crop holds the previous row letter",
        )
        for letter in "JKLMNOPQRS"
    },
    **{
        f"W01-lower-{letter}": (
            LABEL_SHIFT,
            "duplicate reviewed box at g/h and n/o; crop holds the previous row letter",
        )
        for letter in "hijklmnopqrstuvw"
    },
    "W01-lower-g": (CLIPPED_GLYPH, "very narrow box clips the g bowl; prediction still right"),
    # W02 — two duplicate pairs on M and N shift O..S; the lowercase second row lost the
    # v box, so v..z name the following glyph and z lands on the row above.
    **{
        f"W02-upper-{letter}": (
            LABEL_SHIFT,
            "duplicate reviewed boxes on M and N; crop holds the previous row letter",
        )
        for letter in "NOPQRS"
    },
    "W02-upper-M": (CLIPPED_GLYPH, "14 px box keeps only the left slice of the M glyph"),
    "W02-upper-Z": (
        NEIGHBOUR_INTRUSION,
        "part of the adjacent g from the sentence line enters the crop at the right",
    ),
    **{
        f"W02-lower-{letter}": (
            LABEL_SHIFT,
            "missing v box in the second lowercase row; crop holds the following glyph",
        )
        for letter in "vwxyz"
    },
    # W03 — duplicate box on w/x shifts x and y; z is unboxed and x is a w fragment.
    "W03-lower-x": (LABEL_SHIFT, "narrow box on the w right stroke; crop is a w fragment"),
    "W03-lower-y": (LABEL_SHIFT, "crop holds x because of the w/x duplicate box"),
    "W03-lower-z": (LABEL_SHIFT, "crop holds y; the row's own z is unboxed"),
    "W03-lower-a": (LINE_ARTEFACT, "vertical page margin rule crosses the a crop"),
    "W03-lower-w": (CLIPPED_GLYPH, "right edge of the box cuts the last w stroke"),
    # W04 — uppercase row is aligned; lowercase shifts j..s, with m aliased back onto its
    # own label by a duplicate at s/t. The writer's v sits on the page's red rule.
    **{
        f"W04-lower-{letter}": (
            LABEL_SHIFT,
            "missing j box and duplicate s/t boxes; crop holds the previous row letter",
        )
        for letter in "jklmnopqrs"
    },
    "W04-lower-v": (LINE_ARTEFACT, "red page margin rule crosses the v crop"),
    "W04-lower-w": (AMBIGUOUS_FORM, "crop is clear; the lower w/case split is the whole error"),
    # W05 — duplicate pair at l/m restores alignment at n, so i..m name the next glyph.
    **{
        f"W05-lower-{letter}": (
            LABEL_SHIFT,
            "missing i box and duplicate l/m boxes; crop holds the next letter",
        )
        for letter in "ijklm"
    },
    # Genuinely ambiguous written forms, judged by eye on a correctly placed crop.
    "W01-upper-I": (AMBIGUOUS_FORM, "serifed capital I is written like a lowercase j"),
    "W01-lower-a": (AMBIGUOUS_FORM, "single-storey a with a heavy tail reads as g"),
    "W01-lower-y": (AMBIGUOUS_FORM, "y with a shallow tail reads as X"),
    "W02-upper-L": (AMBIGUOUS_FORM, "capital L is a bare vertical stroke with a foot"),
    "W02-upper-G": (AMBIGUOUS_FORM, "G swash closes into an m-like shape"),
    "W02-lower-l": (AMBIGUOUS_FORM, "l is a bare stroke with a base serif"),
    "W03-upper-D": (AMBIGUOUS_FORM, "D is closed at the bottom and reads as O"),
    "W03-upper-J": (AMBIGUOUS_FORM, "J carries a long flat top bar, readable as T"),
    "W03-upper-U": (AMBIGUOUS_FORM, "U without a right stem can read as W"),
    "W03-lower-f": (AMBIGUOUS_FORM, "descender loop of f reads as a t stem"),
    "W03-lower-j": (AMBIGUOUS_FORM, "j hook is short and wide, readable as v"),
    "W03-lower-q": (AMBIGUOUS_FORM, "q bowl closes fully, readable as g"),
    "W03-lower-r": (AMBIGUOUS_FORM, "r is written with a gamma-like arm"),
    "W03-lower-u": (AMBIGUOUS_FORM, "u carries a leading upstroke, readable as w"),
    "W04-upper-H": (AMBIGUOUS_FORM, "H crossbar is faint, silhouette matches M"),
    "W04-upper-Q": (AMBIGUOUS_FORM, "Q is a loop over a long diagonal tail"),
    "W04-upper-U": (AMBIGUOUS_FORM, "U opens on the right and reads as W"),
    "W04-upper-X": (AMBIGUOUS_FORM, "x is written as a looped cross"),
    "W05-upper-I": (AMBIGUOUS_FORM, "serifed I matches a lowercase t silhouette"),
    "W05-upper-J": (AMBIGUOUS_FORM, "J hook is shallow and reads as I"),
    "W05-upper-Q": (AMBIGUOUS_FORM, "Q is a closed loop with a short tail"),
    "W05-upper-U": (AMBIGUOUS_FORM, "U is written as a narrow V"),
}

INK_THRESHOLD = 110
RULE_ROW_FRACTION = 0.55
RULE_COLUMN_LIMIT = 6
MAX_CROP_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Measurement:
    brightness: float
    contrast: float
    ink_fraction: float
    ink_box: tuple[int, int, int, int] | None
    margin_fractions: tuple[float, ...]
    touches_edge: bool
    rule_strength: float
    rule_rows: int


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - older Pillow fallback
        return ImageFont.load_default()


def load_predictions(path: Path) -> dict[str, str]:
    """Read the frozen pilot predictions; refuse anything but the saved raw text."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("volunteer_audit_predictions_missing_or_unsafe")
    predictions: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"volunteer_audit_predictions_blank_line:{number}")
        record = json.loads(line)
        if not isinstance(record, Mapping):
            raise ValueError(f"volunteer_audit_predictions_not_an_object:{number}")
        sample_id = record.get("sample_id")
        text = record.get("raw_prediction")
        if not isinstance(sample_id, str) or not isinstance(text, str):
            raise ValueError(f"volunteer_audit_predictions_record_shape:{number}")
        if sample_id in predictions:
            raise ValueError(f"volunteer_audit_predictions_duplicate:{sample_id}")
        predictions[sample_id] = text
    if len(predictions) != EXPECTED_SAMPLES:
        raise ValueError("volunteer_audit_predictions_count_differs_from_inventory")
    return predictions


def _load_crop(root: Path, sample: ManifestSample) -> Image.Image:
    path = root / sample.crop_path
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"volunteer_audit_crop_missing:{sample.sample_id}")
    if path.stat().st_size > MAX_CROP_BYTES:
        raise ValueError(f"volunteer_audit_crop_too_large:{sample.sample_id}")
    if digest_file(path) != sample.crop_sha256:
        raise ValueError(f"volunteer_audit_crop_changed:{sample.sample_id}")
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError(f"volunteer_audit_crop_format_changed:{sample.sample_id}")
        image.load()
        return image.convert("RGB")


def measure(image: Image.Image) -> Measurement:
    """Mechanical crop measurements; every value is deterministic for fixed pixels."""
    if image.width < 8 or image.height < 8:
        raise ValueError("volunteer_audit_crop_too_small_to_measure")
    grey = image.convert("L")
    width, height = grey.size
    pixels = list(grey.getdata())
    ordered = sorted(pixels)
    brightness = sum(pixels) / len(pixels)
    low = ordered[int(0.05 * (len(ordered) - 1))]
    high = ordered[int(0.95 * (len(ordered) - 1))]
    ink = [index for index, value in enumerate(pixels) if value < INK_THRESHOLD]
    ink_fraction = len(ink) / len(pixels)
    ink_box: tuple[int, int, int, int] | None = None
    margin_fractions = (1.0, 1.0, 1.0, 1.0)
    touches_edge = False
    if ink:
        xs = [index % width for index in ink]
        ys = [index // width for index in ink]
        ink_box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
        margin_fractions = (
            ink_box[0] / width,
            ink_box[1] / height,
            (width - ink_box[2]) / width,
            (height - ink_box[3]) / height,
        )
        touches_edge = min(margin_fractions) <= 0.01
    rule_rows = 0
    for row in range(height):
        start = row * width
        dark = sum(1 for value in pixels[start : start + width] if value < INK_THRESHOLD)
        if dark / width >= RULE_ROW_FRACTION:
            rule_rows += 1
    return Measurement(
        brightness=round(brightness, 3),
        contrast=round(float(high - low), 3),
        ink_fraction=round(ink_fraction, 6),
        ink_box=ink_box,
        margin_fractions=tuple(round(value, 4) for value in margin_fractions),
        touches_edge=touches_edge,
        rule_strength=round(rule_rows / height, 4),
        rule_rows=rule_rows,
    )


def measurement_dict(value: Measurement) -> dict[str, Any]:
    """Plain JSON-safe record for one crop; no dataclass objects reach the report."""
    return {
        "brightness": value.brightness,
        "contrast": value.contrast,
        "ink_fraction": value.ink_fraction,
        "ink_box": list(value.ink_box) if value.ink_box else None,
        "margin_fractions": list(value.margin_fractions),
        "touches_edge": value.touches_edge,
        "rule_strength": value.rule_strength,
        "rule_rows": value.rule_rows,
    }


def _rows(samples: Sequence[ManifestSample]) -> list[list[ManifestSample]]:
    """Group one writer's samples into writing rows by top edge, in reading order."""
    ordered = list(samples)
    rows: list[list[ManifestSample]] = []
    current: list[ManifestSample] = []
    baseline: int | None = None
    for sample in ordered:
        top = sample.letter_box[1]
        if baseline is None or abs(top - baseline) <= 8:
            current.append(sample)
            baseline = top if baseline is None else baseline
            continue
        rows.append(current)
        current = [sample]
        baseline = top
    if current:
        rows.append(current)
    return rows


def box_geometry(manifest: Manifest) -> dict[str, dict[str, Any]]:
    """Reviewed-box geometry per sample: overlap, width and gap against the row median.

    A duplicated reviewed box is visible here without any model output: consecutive
    boxes in one writing row overlap, one of them is far narrower than the row median,
    and the row's trailing glyph receives no box at all.
    """
    geometry: dict[str, dict[str, Any]] = {}
    for writer_id in WRITERS:
        samples = [sample for sample in manifest.samples if sample.writer_id == writer_id]
        for row in _rows(samples):
            widths = sorted(sample.letter_box[2] - sample.letter_box[0] for sample in row)
            median = widths[len(widths) // 2]
            for index, sample in enumerate(row):
                left, _, right, _ = sample.letter_box
                previous = row[index - 1] if index else None
                following = row[index + 1] if index + 1 < len(row) else None
                overlap_previous = (
                    max(0, previous.letter_box[2] - left) if previous is not None else 0
                )
                overlap_next = (
                    max(0, right - following.letter_box[0]) if following is not None else 0
                )
                geometry[sample.sample_id] = {
                    "row_index": index,
                    "row_size": len(row),
                    "box_width": right - left,
                    "row_median_width": median,
                    "narrow": (right - left) < 0.6 * median,
                    "overlap_previous_px": overlap_previous,
                    "overlap_next_px": overlap_next,
                    "duplicate_pair": overlap_previous > 0 or overlap_next > 0,
                }
    return geometry


def shift_runs(manifest: Manifest, predictions: Mapping[str, str]) -> dict[str, list[str]]:
    """Model-based signal: maximal runs predicted as the neighbouring row letter.

    This is evidence, not proof: it uses the saved predictions, so a run can only be
    read together with the reviewer table and the box geometry.
    """
    runs: dict[str, list[str]] = {}
    for writer_id in WRITERS:
        samples = [sample for sample in manifest.samples if sample.writer_id == writer_id]
        for row in _rows(samples):
            letters = [sample.truth for sample in row]
            run: list[str] = []
            for index in range(1, len(row)):
                sample = row[index]
                prediction = predictions.get(sample.sample_id, "")
                if prediction in (letters[index - 1], letters[index - 1].swapcase()):
                    run.append(sample.sample_id)
                else:
                    if len(run) >= 2:
                        for member in run:
                            runs[member] = run.copy()
                    run = []
            if len(run) >= 2:
                for member in run:
                    runs[member] = run.copy()
    return runs


def classify(predictions: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Attach the fixed reviewer category to every sample, correct ones included.

    A crop can be defective and still be scored correct (for example a shifted crop the
    model happened to read as the label), so the category is independent of correctness.
    """
    outcome: dict[str, dict[str, Any]] = {}
    for sample_id, prediction in predictions.items():
        truth = sample_id.rsplit("-", 1)[1]
        correct = prediction == truth
        folded = len(prediction) == 1 and prediction.casefold() == truth.casefold()
        reviewed = REVIEW_TABLE.get(sample_id)
        if reviewed is not None:
            category = reviewed[0]
        elif correct:
            category = RECOGNIZED
        else:
            category = RECOGNITION_ERROR
        outcome[sample_id] = {
            "truth": truth,
            "prediction": prediction,
            "correct": correct,
            "case_only": bool(folded and not correct),
            "category": category,
            "crop_defect": category in CROP_DEFECT_CATEGORIES,
            "reviewer_note": reviewed[1] if reviewed else "",
        }
    return outcome


def _error_sheet(
    samples: Sequence[ManifestSample],
    crops: Mapping[str, Image.Image],
    outcome: Mapping[str, Mapping[str, Any]],
    *,
    writer_id: str,
    columns: int = 5,
    cell: int = 208,
) -> bytes:
    """One private review sheet: crop, writer, truth, raw prediction and category."""
    errors = [sample for sample in samples if not outcome[sample.sample_id]["correct"]]
    rows = max(1, (len(errors) + columns - 1) // columns)
    header, footer = 54, 34
    width = columns * cell
    height = header + rows * (cell + 62) + footer
    sheet = Image.new("RGB", (width, height), (247, 244, 238))
    draw = ImageDraw.Draw(sheet)
    title_font, label_font, small_font = _font(18), _font(13), _font(11)
    draw.text(
        (10, 9),
        f"{writer_id} - {len(errors)} errors - PRIVATE error review, do not distribute",
        fill=(40, 30, 25),
        font=title_font,
    )
    draw.text(
        (10, 32),
        "Category is a reviewer judgment over the frozen crops. Truth and prediction are "
        "the saved pilot values.",
        fill=(90, 78, 70),
        font=small_font,
    )
    for index, sample in enumerate(errors):
        record = outcome[sample.sample_id]
        column, row = index % columns, index // columns
        x = column * cell
        y = header + row * (cell + 62)
        thumb = crops[sample.sample_id].resize((cell - 12, cell - 12), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x + 6, y + 2))
        draw.rectangle((x + 5, y + 1, x + cell - 7, y + cell - 10), outline=(198, 186, 176))
        draw.text((x + 6, y + cell - 8), sample.sample_id, fill=(35, 32, 30), font=label_font)
        draw.text(
            (x + 6, y + cell + 6),
            f"truth={record['truth']}  pred={record['prediction']!r}",
            fill=(35, 32, 30),
            font=small_font,
        )
        category = str(record["category"])
        if record["case_only"]:
            category += " (case-only)"
        colour = (150, 100, 20) if record["case_only"] else (170, 40, 30)
        draw.text((x + 6, y + cell + 22), category, fill=colour, font=label_font)
    draw.text(
        (10, height - footer + 6),
        "Exploratory five-writer pilot. Isolated letter crops only; no sentences, digits or "
        "symbols. Original crops unchanged.",
        fill=(90, 78, 70),
        font=small_font,
    )
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def build(run: Path) -> dict[str, Any]:
    """Write the private error-review sheets and the aggregate audit report."""
    root = private_run_directory(run)
    manifest = load_manifest(root / MANIFEST_NAME)
    predictions = load_predictions(root / PREDICTIONS_NAME)
    manifest_ids = {sample.sample_id for sample in manifest.samples}
    if set(predictions) != manifest_ids:
        raise ValueError("volunteer_audit_predictions_do_not_match_the_manifest")
    outcome = classify(predictions)
    geometry = box_geometry(manifest)
    runs = shift_runs(manifest, predictions)
    crops: dict[str, Image.Image] = {}
    measurements: dict[str, dict[str, Any]] = {}
    for sample in manifest.samples:
        image = _load_crop(root, sample)
        crops[sample.sample_id] = image
        measurements[sample.sample_id] = measurement_dict(measure(image))
    outputs: dict[str, bytes] = {}
    for writer_id in WRITERS:
        writer_samples = [sample for sample in manifest.samples if sample.writer_id == writer_id]
        outputs[f"{REVIEW_DIR}/{writer_id}.png"] = _error_sheet(
            writer_samples, crops, outcome, writer_id=writer_id
        )
    report = aggregate(manifest, outcome, measurements, geometry, runs)
    report["version"] = AUDIT_VERSION
    report["review_version"] = REVIEW_VERSION
    report["run"] = str(root)
    report["crop_manifest_sha256"] = manifest.sha256
    report["predictions_sha256"] = digest_file(root / PREDICTIONS_NAME)
    report["pilot_report_sha256"] = (
        digest_file(root / REPORT_NAME) if (root / REPORT_NAME).is_file() else None
    )
    report["baseline_modified"] = False
    report["labels_changed"] = 0
    report["model_execution"] = False
    report["training_performed"] = False
    report["samples"] = {
        sample.sample_id: {
            **outcome[sample.sample_id],
            **geometry[sample.sample_id],
            "measurement": measurements[sample.sample_id],
            "shift_run_signalled": sample.sample_id in runs,
        }
        for sample in manifest.samples
    }
    outputs[AUDIT_NAME] = (
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    review_dir = root / REVIEW_DIR
    if review_dir.exists() and not review_dir.is_dir():
        raise ValueError("volunteer_audit_review_path_is_not_a_directory")
    written = 0
    for relative, payload in sorted(outputs.items()):
        target = root / relative
        if target.is_symlink():
            raise ValueError(f"volunteer_audit_output_must_not_be_symlink:{relative}")
        if target.exists():
            if target.read_bytes() != payload:
                raise FileExistsError(f"volunteer_audit_existing_output_differs:{relative}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.with_name(target.name + ".partial")
        with temporary.open("wb") as stream:
            stream.write(payload)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        written += 1
    return {**report["aggregate"], "files_written": written, "run": str(root)}


def aggregate(
    manifest: Manifest,
    outcome: Mapping[str, Mapping[str, Any]],
    measurements: Mapping[str, Mapping[str, Any]],
    geometry: Mapping[str, Mapping[str, Any]],
    runs: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """Aggregate counts, kept deliberately separate from the frozen pilot report."""
    sample_categories = Counter(str(record["category"]) for record in outcome.values())
    error_categories = Counter(
        str(record["category"]) for record in outcome.values() if not record["correct"]
    )
    per_writer: dict[str, Any] = {}
    for writer_id in WRITERS:
        ids = [sample.sample_id for sample in manifest.samples if sample.writer_id == writer_id]
        total = len(ids)
        correct = sum(1 for sample_id in ids if outcome[sample_id]["correct"])
        case_only = sum(1 for sample_id in ids if outcome[sample_id]["case_only"])
        defective = [sample_id for sample_id in ids if outcome[sample_id]["crop_defect"]]
        clean = [sample_id for sample_id in ids if not outcome[sample_id]["crop_defect"]]
        clean_correct = sum(1 for sample_id in clean if outcome[sample_id]["correct"])
        per_writer[writer_id] = {
            "samples": total,
            "correct": correct,
            "accuracy": correct / total,
            "case_only_errors": case_only,
            "crop_defect_samples": len(defective),
            "crop_defect_error_samples": sum(
                1 for sample_id in defective if not outcome[sample_id]["correct"]
            ),
            "clean_crop_samples": len(clean),
            "clean_crop_correct": clean_correct,
            "clean_crop_accuracy": clean_correct / len(clean) if clean else None,
        }
    total = len(outcome)
    correct = sum(1 for record in outcome.values() if record["correct"])
    folded = sum(
        1
        for record in outcome.values()
        if record["correct"]
        or (
            len(record["prediction"]) == 1
            and record["prediction"].casefold() == record["truth"].casefold()
        )
    )
    defective_ids = _defective(outcome)
    clean_ids = [sample_id for sample_id in outcome if sample_id not in defective_ids]
    clean_correct = sum(1 for sample_id in clean_ids if outcome[sample_id]["correct"])
    ambiguous_letters = "lqr"
    per_letter: dict[str, Any] = {}
    for label in UPPER + LOWER:
        ids = [sample_id for sample_id, record in outcome.items() if record["truth"] == label]
        matched = sum(1 for sample_id in ids if outcome[sample_id]["correct"])
        letter_defective = [sample_id for sample_id in ids if outcome[sample_id]["crop_defect"]]
        per_letter[label] = {
            "samples": len(ids),
            "correct": matched,
            "crop_defect_samples": len(letter_defective),
            "crop_defect_errors": sum(
                1 for sample_id in letter_defective if not outcome[sample_id]["correct"]
            ),
            "clean_crop_samples": len(ids) - len(letter_defective),
            "clean_crop_correct": sum(
                1
                for sample_id in ids
                if not outcome[sample_id]["crop_defect"] and outcome[sample_id]["correct"]
            ),
        }
    touching = sum(1 for record in measurements.values() if record["touches_edge"])
    ruled = sum(1 for record in measurements.values() if record["rule_rows"] > 0)
    narrow = sum(1 for record in geometry.values() if record["narrow"])
    duplicated = sum(1 for record in geometry.values() if record["duplicate_pair"])
    return {
        "aggregate": {
            "samples": total,
            "correct": correct,
            "accuracy": correct / total,
            "casefolded_shape_accuracy": folded / total,
            "case_only_errors": folded - correct,
            "other_errors": total - folded,
            "sample_categories": dict(sorted(sample_categories.items())),
            "error_categories": dict(sorted(error_categories.items())),
            "crop_defect_samples": len(defective_ids),
            "crop_defect_error_samples": sum(
                1 for sample_id in defective_ids if not outcome[sample_id]["correct"]
            ),
            "crop_defect_scored_correct": sum(
                1 for sample_id in defective_ids if outcome[sample_id]["correct"]
            ),
            "clean_crop_samples": len(clean_ids),
            "clean_crop_correct": clean_correct,
            "clean_crop_accuracy": clean_correct / len(clean_ids) if clean_ids else None,
            "clean_crop_casefolded_accuracy": sum(
                1
                for sample_id in clean_ids
                if outcome[sample_id]["correct"] or outcome[sample_id]["case_only"]
            )
            / len(clean_ids)
            if clean_ids
            else None,
            "ambiguous_letters": {letter: per_letter[letter] for letter in ambiguous_letters},
            "per_writer": per_writer,
            "per_letter": per_letter,
            "mechanical": {
                "crops_touching_an_edge": touching,
                "crops_with_a_ruled_row": ruled,
                "narrow_boxes": narrow,
                "boxes_in_a_duplicate_pair": duplicated,
                "mean_brightness": round(
                    sum(record["brightness"] for record in measurements.values())
                    / len(measurements),
                    3,
                ),
                "mean_contrast": round(
                    sum(record["contrast"] for record in measurements.values()) / len(measurements),
                    3,
                ),
                "mean_ink_fraction": round(
                    sum(record["ink_fraction"] for record in measurements.values())
                    / len(measurements),
                    6,
                ),
                "mean_rule_strength": round(
                    sum(record["rule_strength"] for record in measurements.values())
                    / len(measurements),
                    4,
                ),
            },
            "categories": list(ALL_CATEGORIES),
            "crop_defect_categories": sorted(CROP_DEFECT_CATEGORIES),
            "review_is_reviewer_judgment": True,
            "frozen_pilot_accuracy": 0.5076923076923077,
            "frozen_pilot_note": (
                "50.77% remains the frozen pilot result; the clean-crop figures are a "
                "reviewer-judged diagnostic subset and are not a replacement measurement"
            ),
        }
    }


def _defective(outcome: Mapping[str, Mapping[str, Any]]) -> set[str]:
    return {sample_id for sample_id, record in outcome.items() if record["crop_defect"]}


def main() -> None:  # pragma: no cover - exercised through the owner wrapper
    import sys

    if len(sys.argv) != 2:
        print("usage: python -m research.english.volunteer.audit <private-run>", file=sys.stderr)
        raise SystemExit(2)
    value = build(Path(sys.argv[1]))
    print(json.dumps(value, sort_keys=True, allow_nan=False))


__all__ = [
    "AMBIGUOUS_FORM",
    "AUDIT_NAME",
    "AUDIT_VERSION",
    "CATEGORIES",
    "CLIPPED_GLYPH",
    "CROP_DEFECT_CATEGORIES",
    "FRAGMENT_OR_EMPTY",
    "LABEL_SHIFT",
    "LINE_ARTEFACT",
    "REVIEW_DIR",
    "REVIEW_TABLE",
    "REVIEW_VERSION",
    "aggregate",
    "box_geometry",
    "build",
    "classify",
    "load_predictions",
    "measure",
    "shift_runs",
]
