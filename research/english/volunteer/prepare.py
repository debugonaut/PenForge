"""Data-only preparation for the private five-writer volunteer pilot.

This stage hashes the five original pages, copies them byte-for-byte into the
private run directory, cuts the reviewed letter boxes, pads each crop to a
square white canvas, writes the crop manifest and renders contact sheets for
inspection. It never imports Torch, Transformers or Safetensors and a meta-path
guard makes such an import fail while preparation is running.

The primary condition uses the original page pixels: no contrast adjustment,
no thresholding, no ruled-line erasure and no redrawing.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import io
import json
import os
import sys
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from research.english.volunteer.manifest import (
    CROP_GENERATION_VERSION,
    EXPECTED_SAMPLES,
    LOWER,
    MANIFEST_VERSION,
    UPPER,
    WRITERS,
    ManifestSample,
    ReviewedWriter,
    expected_sample_id,
    validate_coordinates,
)
from research.pretrained.artifacts import ROOT

MARGIN_FRACTION = 0.04
CROP_SIDE = 384
RESAMPLING = "lanczos"
SQUARE_PADDING = "centered_white_rgb"
NO_MODEL_MODULES = frozenset({"torch", "transformers", "safetensors"})
COORDINATES_NAME = "coordinates.json"
MANIFEST_NAME = "crop-manifest.json"
PREPARED_NAME = "prepared.json"
PREDICTIONS_NAME = "predictions-private.jsonl"
REPORT_NAME = "report.json"
CONTACT_SHEET_DIR = "contact-sheets"
SOURCE_DIR = "sources"
CROP_DIR = "crops/images"
MAX_SOURCE_BYTES = 40 * 1024 * 1024


class ModelFrameworkImport(importlib.abc.MetaPathFinder):
    """Fail preparation if any code tries to import a model framework."""

    def find_spec(self, fullname: str, path: object = None, target: object = None):
        if fullname.split(".")[0] in NO_MODEL_MODULES:
            raise ValueError("volunteer_prepare_must_not_import_model_frameworks")
        return None


@contextmanager
def model_frameworks_blocked():
    installed = {name for name in NO_MODEL_MODULES if name in sys.modules}
    if installed:
        raise ValueError(
            "volunteer_prepare_started_with_model_frameworks_loaded:" + ",".join(sorted(installed))
        )
    guard = ModelFrameworkImport()
    sys.meta_path.insert(0, guard)
    try:
        yield
    finally:
        sys.meta_path.remove(guard)


def private_run_directory(run: Path) -> Path:
    """A volunteer run must be private: inside research/runs, or outside the repo."""
    raw = Path(run)
    if raw.is_symlink():
        raise ValueError("volunteer_run_root_must_not_be_symlink")
    resolved = raw.expanduser().resolve()
    if resolved == ROOT or resolved.is_relative_to(ROOT):
        private_root = (ROOT / "research/runs").resolve()
        if resolved == private_root or not resolved.is_relative_to(private_root):
            raise ValueError("volunteer_run_must_live_under_research_runs")
    return resolved


def _write_json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("wb") as stream:
        stream.write(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _load_coordinates(path: Path) -> tuple[ReviewedWriter, ...]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("volunteer_coordinates_missing_or_unsafe")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return validate_coordinates(payload)


def _read_source(writer: ReviewedWriter) -> tuple[Image.Image, bytes]:
    raw = Path(writer.source.source_path)
    if raw.is_symlink() or not raw.is_file():
        raise ValueError(f"volunteer_source_missing_or_unsafe:{writer.source.writer_id}")
    payload = raw.read_bytes()
    if len(payload) != writer.source.source_bytes:
        raise ValueError(f"volunteer_source_size_changed:{writer.source.writer_id}")
    if hashlib.sha256(payload).hexdigest() != writer.source.source_sha256:
        raise ValueError(f"volunteer_source_hash_changed:{writer.source.writer_id}")
    with Image.open(io.BytesIO(payload)) as probe:
        if probe.width != writer.source.page_width or probe.height != writer.source.page_height:
            raise ValueError(f"volunteer_source_dimensions_changed:{writer.source.writer_id}")
        image = probe.convert("RGB")
    return image, payload


def crop_letter(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[Image.Image, int]:
    """Cut one reviewed box, pad it to a square white canvas and normalise size.

    Only whitespace is added and the square canvas is scaled uniformly, so the
    glyph is never stretched and the paper pixels keep their original contrast.
    """
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    margin = max(2, round(MARGIN_FRACTION * max(width, height)))
    left = max(0, x0 - margin)
    top = max(0, y0 - margin)
    right = min(image.width, x1 + margin)
    bottom = min(image.height, y1 + margin)
    piece = image.crop((left, top, right, bottom))
    side = max(piece.width, piece.height)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(piece, ((side - piece.width) // 2, (side - piece.height) // 2))
    if side != CROP_SIDE:
        canvas = canvas.resize((CROP_SIDE, CROP_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=False)
    return canvas, margin


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - older Pillow fallback
        return ImageFont.load_default()


def contact_sheet(
    samples: Sequence[ManifestSample],
    crops: Mapping[str, Image.Image],
    *,
    writer_id: str,
    columns: int = 13,
    cell: int = 128,
    context: str | None = None,
) -> bytes:
    """Render every crop with its sample ID and expected label for inspection."""
    rows = (len(samples) + columns - 1) // columns
    header = 46
    footer = 30
    width = columns * cell
    height = header + rows * (cell + 26) + footer
    sheet = Image.new("RGB", (width, height), (247, 244, 238))
    draw = ImageDraw.Draw(sheet)
    title_font, label_font, small_font = _font(17), _font(14), _font(12)
    draw.text(
        (10, 9),
        f"{writer_id} - {len(samples)} reviewed letter crops - PRIVATE, do not distribute",
        fill=(40, 30, 25),
        font=title_font,
    )
    draw.text(
        (10, 28),
        "Each cell shows the sample ID and the exact case-sensitive truth label.",
        fill=(90, 78, 70),
        font=small_font,
    )
    for index, sample in enumerate(samples):
        column = index % columns
        row = index // columns
        x = column * cell
        y = header + row * (cell + 26)
        thumb = crops[sample.sample_id].resize((cell - 12, cell - 12), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x + 6, y + 2))
        draw.rectangle((x + 5, y + 1, x + cell - 6, y + cell - 8), outline=(198, 186, 176))
        draw.text((x + 6, y + cell - 6), sample.sample_id, fill=(35, 32, 30), font=label_font)
        draw.text(
            (x + 6, y + cell + 8), f"truth={sample.truth}", fill=(120, 60, 40), font=small_font
        )
    draw.text(
        (10, height - footer + 6),
        context
        or "Exploratory five-writer pilot. Isolated letter crops only; no sentences, digits or symbols.",
        fill=(90, 78, 70),
        font=small_font,
    )
    buffer = io.BytesIO()
    sheet.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _source_copy_name(writer: ReviewedWriter) -> str:
    suffix = Path(writer.source.source_path).suffix.lower() or ".img"
    if suffix not in {".jpeg", ".jpg", ".png", ".webp"}:
        suffix = ".img"
    return f"{SOURCE_DIR}/{writer.source.writer_id}{suffix}"


def _inventory_order(writers: Sequence[ReviewedWriter]) -> list[str]:
    """Canonical reading order: each writer's A–Z row, then its a–z row."""
    return [
        expected_sample_id(writer.source.writer_id, group, label)
        for writer in writers
        for group, letters in (("upper", UPPER), ("lower", LOWER))
        for label in letters
    ]


def _build_manifest(
    writers: Sequence[ReviewedWriter],
    prepared: Mapping[str, tuple[ManifestSample, Image.Image]],
) -> dict[str, Any]:
    samples = [
        {
            "sample_id": sample.sample_id,
            "writer_id": sample.writer_id,
            "group": sample.group,
            "truth": sample.truth,
            "letter_box": list(sample.letter_box),
            "crop_box": list(sample.crop_box),
            "crop_width": sample.crop_width,
            "crop_height": sample.crop_height,
            "crop_sha256": sample.crop_sha256,
            "crop_path": sample.crop_path,
            "source_sha256": sample.source_sha256,
            "crop_generation_version": sample.crop_generation_version,
            "review_status": sample.review_status,
        }
        for sample, _ in (prepared[sample_id] for sample_id in _inventory_order(writers))
    ]
    return {
        "version": MANIFEST_VERSION,
        "pilot": "private_five_writer_exploratory_pilot",
        "scope": "isolated_uppercase_and_lowercase_letter_crops_only",
        "crop_generation": {
            "version": CROP_GENERATION_VERSION,
            "margin_fraction": MARGIN_FRACTION,
            "square_padding": SQUARE_PADDING,
            "output_side": CROP_SIDE,
            "resampling": RESAMPLING,
            "contrast_adjustment": "none_original_page_pixels",
            "line_erasure": "none",
        },
        "sources": [
            {
                "writer_id": writer.source.writer_id,
                "source_path": writer.source.source_path,
                "source_sha256": writer.source.source_sha256,
                "source_bytes": writer.source.source_bytes,
                "copied_path": _source_copy_name(writer),
                "page_width": writer.source.page_width,
                "page_height": writer.source.page_height,
            }
            for writer in writers
        ],
        "included_samples": len(samples),
        "excluded": [],
        "samples": samples,
    }


def _prepared_payload(
    writers: Sequence[ReviewedWriter],
    manifest_bytes: bytes,
    *,
    coordinates_sha256: str,
    crop_count: int,
) -> dict[str, Any]:
    return {
        "status": "prepared_not_evaluated",
        "pilot": "private_five_writer_exploratory_pilot",
        "model_execution": False,
        "training_performed": False,
        "writers": [writer.source.writer_id for writer in writers],
        "expected_samples": EXPECTED_SAMPLES,
        "included_samples": crop_count,
        "excluded_samples": 0,
        "crop_generation_version": CROP_GENERATION_VERSION,
        "coordinates_sha256": coordinates_sha256,
        "crop_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "sources": [
            {
                "writer_id": writer.source.writer_id,
                "source_sha256": writer.source.source_sha256,
                "source_bytes": writer.source.source_bytes,
            }
            for writer in writers
        ],
        "next_stage": "owner_evaluate",
    }


def _plan_outputs(run: Path, coordinates_path: Path) -> dict[str, bytes]:
    writers = _load_coordinates(coordinates_path)
    coordinates_bytes = coordinates_path.read_bytes()
    outputs: dict[str, bytes] = {}
    prepared: dict[str, tuple[ManifestSample, Image.Image]] = {}
    sheet_crops: dict[str, dict[str, Image.Image]] = {writer: {} for writer in WRITERS}
    for writer in writers:
        image, payload = _read_source(writer)
        copy_name = _source_copy_name(writer)
        outputs[copy_name] = payload
        for box in writer.boxes:
            sample_id = expected_sample_id(writer.source.writer_id, box.group, box.label)
            crop, _margin = crop_letter(image, (box.x0, box.y0, box.x1, box.y1))
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG", optimize=False)
            crop_bytes = buffer.getvalue()
            digest = hashlib.sha256(crop_bytes).hexdigest()
            outputs[f"{CROP_DIR}/{digest}.png"] = crop_bytes
            margin = max(2, round(MARGIN_FRACTION * max(box.width, box.height)))
            prepared[sample_id] = (
                ManifestSample(
                    sample_id=sample_id,
                    writer_id=writer.source.writer_id,
                    group=box.group,
                    truth=box.label,
                    letter_box=(box.x0, box.y0, box.x1, box.y1),
                    crop_box=(
                        max(0, box.x0 - margin),
                        max(0, box.y0 - margin),
                        min(image.width, box.x1 + margin),
                        min(image.height, box.y1 + margin),
                    ),
                    crop_sha256=digest,
                    crop_path=f"{CROP_DIR}/{digest}.png",
                    source_sha256=writer.source.source_sha256,
                    crop_generation_version=CROP_GENERATION_VERSION,
                    review_status="reviewed_agent",
                    crop_width=crop.width,
                    crop_height=crop.height,
                ),
                crop,
            )
            sheet_crops[writer.source.writer_id][sample_id] = crop
    manifest = _build_manifest(writers, prepared)
    manifest_bytes = _write_json_bytes(manifest)
    outputs[MANIFEST_NAME] = manifest_bytes
    inventory = _inventory_order(writers)
    for writer_id in WRITERS:
        samples = [
            prepared[sample_id][0]
            for sample_id in inventory
            if prepared[sample_id][0].writer_id == writer_id
        ]
        outputs[f"{CONTACT_SHEET_DIR}/{writer_id}.png"] = contact_sheet(
            samples, sheet_crops[writer_id], writer_id=writer_id
        )
    outputs[PREPARED_NAME] = _write_json_bytes(
        _prepared_payload(
            writers,
            manifest_bytes,
            coordinates_sha256=hashlib.sha256(coordinates_bytes).hexdigest(),
            crop_count=len(prepared),
        )
    )
    if len(prepared) != EXPECTED_SAMPLES:
        raise ValueError("volunteer_crop_count_differs_from_expected_inventory")
    return outputs


def prepare(run: Path, coordinates_path: Path | None = None) -> dict[str, Any]:
    """Cut and record all crops. Safe to re-run only while nothing has evaluated."""
    with model_frameworks_blocked():
        root = private_run_directory(run)
        coordinates = Path(coordinates_path) if coordinates_path else root / COORDINATES_NAME
        if (root / REPORT_NAME).exists() or (root / PREDICTIONS_NAME).exists():
            raise FileExistsError("volunteer_run_already_evaluated_use_a_new_output_directory")
        outputs = _plan_outputs(root, coordinates)
        # Deterministic: an identical re-run writes nothing and changes nothing.
        written = 0
        for relative, payload in outputs.items():
            target = root / relative
            if target.is_symlink():
                raise ValueError(f"volunteer_output_must_not_be_symlink:{relative}")
            if target.exists():
                if target.read_bytes() != payload:
                    raise FileExistsError(f"volunteer_existing_preparation_differs:{relative}")
                continue
            _atomic_write(target, payload)
            written += 1
        receipt = json.loads((root / PREPARED_NAME).read_text(encoding="utf-8"))
        return {
            "status": "prepared_not_evaluated" if written else "verified_existing_preparation",
            "run": str(root),
            "included_samples": receipt["included_samples"],
            "expected_samples": EXPECTED_SAMPLES,
            "writers": receipt["writers"],
            "files_written": written,
            "crop_manifest_sha256": receipt["crop_manifest_sha256"],
            "model_execution": False,
            "training_performed": False,
        }
