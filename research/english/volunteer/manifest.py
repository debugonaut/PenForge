"""Private volunteer crop manifest and its strict, data-only validation.

The manifest is the single source of truth for the pilot: writer identity, the
original page bytes and hash, one reviewed bounding box per case-sensitive
letter, the content-addressed crop that was cut from it and the crop generation
version. Everything here is pure metadata work; importing this module never
imports Torch, Transformers or Safetensors and never opens a source page.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WRITERS: tuple[str, ...] = ("W01", "W02", "W03", "W04", "W05")
UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LOWER = "abcdefghijklmnopqrstuvwxyz"
EXPECTED_SAMPLES_PER_WRITER = len(UPPER) + len(LOWER)
EXPECTED_SAMPLES = EXPECTED_SAMPLES_PER_WRITER * len(WRITERS)

COORDINATES_VERSION = "penforge-volunteer-coordinates-v1"
MANIFEST_VERSION = "penforge-volunteer-crop-manifest-v1"
CROP_GENERATION_VERSION = "penforge-volunteer-crop-v1"
REVIEW_STATUSES = frozenset({"reviewed_agent", "reviewed_owner"})
GROUPS = ("upper", "lower")
_HEX = set("0123456789abcdef")
_MISSING = object()


def _require(mapping: Mapping[str, Any], key: str, label: str) -> Any:
    value = mapping.get(key, _MISSING)
    if value is _MISSING:
        raise ValueError(f"volunteer_{label}_missing_field:{key}")
    return value


def _text(value: object, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"volunteer_{field}_must_be_bounded_text")
    if "\x00" in value:
        raise ValueError(f"volunteer_{field}_must_not_contain_nul")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX:
        raise ValueError(f"volunteer_{field}_must_be_lowercase_sha256")
    return value


def _positive_int(value: object, field: str, *, maximum: int = 100_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"volunteer_{field}_must_be_bounded_positive_integer")
    return value


def _non_negative_int(value: object, field: str, *, maximum: int = 100_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"volunteer_{field}_must_be_bounded_non_negative_integer")
    return value


def _sequence(value: object, field: str, *, length: int | None = None) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ValueError(f"volunteer_{field}_must_be_a_list")
    if length is not None and len(value) != length:
        raise ValueError(f"volunteer_{field}_must_contain_{length}_items")
    return value


@dataclass(frozen=True)
class Box:
    label: str
    group: str
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass(frozen=True)
class SourceImage:
    writer_id: str
    source_path: str
    source_sha256: str
    source_bytes: int
    page_width: int
    page_height: int


@dataclass(frozen=True)
class ReviewedWriter:
    source: SourceImage
    boxes: tuple[Box, ...]

    def boxes_for(self, group: str) -> tuple[Box, ...]:
        return tuple(box for box in self.boxes if box.group == group)


def _parse_box(raw: object, *, writer_id: str, width: int, height: int) -> Box:
    if not isinstance(raw, Mapping):
        raise ValueError("volunteer_box_must_be_an_object")
    label = _text(_require(raw, "label", "box"), "box_label", limit=1)
    group = _text(_require(raw, "group", "box"), "box_group", limit=8)
    if group not in GROUPS:
        raise ValueError("volunteer_box_group_must_be_upper_or_lower")
    expected = UPPER if group == "upper" else LOWER
    if label not in expected:
        raise ValueError(f"volunteer_box_label_case_mismatch:{writer_id}:{label}")
    x0 = _non_negative_int(_require(raw, "x0", "box"), "box_x0")
    y0 = _non_negative_int(_require(raw, "y0", "box"), "box_y0")
    x1 = _positive_int(_require(raw, "x1", "box"), "box_x1")
    y1 = _positive_int(_require(raw, "y1", "box"), "box_y1")
    if not (x0 < x1 <= width and y0 < y1 <= height):
        raise ValueError(f"volunteer_box_outside_page:{writer_id}:{label}")
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError(f"volunteer_box_too_small:{writer_id}:{label}")
    return Box(label=label, group=group, x0=x0, y0=y0, x1=x1, y1=y1)


def validate_coordinates(payload: object) -> tuple[ReviewedWriter, ...]:
    """Validate the reviewed crop-box table before any image is opened.

    The table must declare exactly five distinct writers, each with the exact
    case-sensitive A–Z then a–z inventory in reading order. Coordinates are
    original-page pixels, so a box outside its own page is a hard error.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("volunteer_coordinates_must_be_an_object")
    if payload.get("version") != COORDINATES_VERSION:
        raise ValueError("volunteer_coordinates_version_changed")
    raw_writers = _sequence(_require(payload, "writers", "coordinates"), "writers", length=5)
    writers: list[ReviewedWriter] = []
    for position, (writer_id, raw) in enumerate(zip(WRITERS, raw_writers, strict=True)):
        if not isinstance(raw, Mapping):
            raise ValueError("volunteer_writer_must_be_an_object")
        if _require(raw, "writer_id", "writer") != writer_id:
            raise ValueError(f"volunteer_writer_order_changed:{position}")
        source = SourceImage(
            writer_id=writer_id,
            source_path=_text(_require(raw, "source_path", "writer"), "source_path"),
            source_sha256=_digest(_require(raw, "source_sha256", "writer"), "source_sha256"),
            source_bytes=_positive_int(
                _require(raw, "source_bytes", "writer"),
                "source_bytes",
                maximum=64 * 1024 * 1024,
            ),
            page_width=_positive_int(_require(raw, "page_width", "writer"), "page_width"),
            page_height=_positive_int(_require(raw, "page_height", "writer"), "page_height"),
        )
        if not Path(source.source_path).is_absolute():
            raise ValueError(f"volunteer_source_path_must_be_absolute:{writer_id}")
        raw_boxes = _sequence(_require(raw, "boxes", "writer"), "boxes", length=52)
        boxes = tuple(
            _parse_box(box, writer_id=writer_id, width=source.page_width, height=source.page_height)
            for box in raw_boxes
        )
        if "".join(box.label for box in boxes) != UPPER + LOWER:
            raise ValueError(f"volunteer_label_order_changed:{writer_id}")
        if tuple(box.group for box in boxes) != ("upper",) * 26 + ("lower",) * 26:
            raise ValueError(f"volunteer_group_order_changed:{writer_id}")
        writers.append(ReviewedWriter(source=source, boxes=boxes))
    hashes = [writer.source.source_sha256 for writer in writers]
    paths = [writer.source.source_path for writer in writers]
    if len(set(hashes)) != len(hashes) or len(set(paths)) != len(paths):
        raise ValueError("volunteer_writers_must_be_distinct_pages")
    return tuple(writers)


@dataclass(frozen=True)
class ManifestSample:
    sample_id: str
    writer_id: str
    group: str
    truth: str
    letter_box: tuple[int, int, int, int]
    crop_box: tuple[int, int, int, int]
    crop_sha256: str
    crop_path: str
    source_sha256: str
    crop_generation_version: str
    review_status: str
    crop_width: int
    crop_height: int


@dataclass(frozen=True)
class Manifest:
    path: Path
    sha256: str
    samples: tuple[ManifestSample, ...]
    sources: tuple[dict[str, Any], ...]
    crop_generation: dict[str, Any]
    excluded: tuple[dict[str, Any], ...]

    @property
    def by_id(self) -> dict[str, ManifestSample]:
        return {sample.sample_id: sample for sample in self.samples}


def expected_sample_id(writer_id: str, group: str, label: str) -> str:
    return f"{writer_id}-{group}-{label}"


def _parse_sample(raw: object, *, index: int) -> ManifestSample:
    if not isinstance(raw, Mapping):
        raise ValueError(f"volunteer_sample_{index}_must_be_an_object")
    writer_id = _text(_require(raw, "writer_id", "sample"), "sample_writer")
    if writer_id not in WRITERS:
        raise ValueError(f"volunteer_sample_unknown_writer:{writer_id}")
    group = _text(_require(raw, "group", "sample"), "sample_group", limit=8)
    if group not in GROUPS:
        raise ValueError(f"volunteer_sample_unknown_group:{group}")
    truth = _text(_require(raw, "truth", "sample"), "sample_truth", limit=1)
    expected = UPPER if group == "upper" else LOWER
    if truth not in expected:
        raise ValueError(f"volunteer_sample_truth_case_mismatch:{index}")
    sample_id = _text(_require(raw, "sample_id", "sample"), "sample_id", limit=32)
    if sample_id != expected_sample_id(writer_id, group, truth):
        raise ValueError(f"volunteer_sample_id_mismatch:{index}")
    raw_box = _sequence(_require(raw, "letter_box", "sample"), "sample_letter_box", length=4)
    letter_box = tuple(_non_negative_int(value, "sample_letter_box") for value in raw_box)
    if not (letter_box[0] < letter_box[2] and letter_box[1] < letter_box[3]):
        raise ValueError(f"volunteer_sample_letter_box_degenerate:{sample_id}")
    raw_crop = _sequence(_require(raw, "crop_box", "sample"), "sample_crop_box", length=4)
    crop_box = tuple(_non_negative_int(value, "sample_crop_box") for value in raw_crop)
    if not (crop_box[0] < crop_box[2] and crop_box[1] < crop_box[3]):
        raise ValueError(f"volunteer_sample_crop_box_degenerate:{sample_id}")
    if not (
        crop_box[0] <= letter_box[0]
        and crop_box[1] <= letter_box[1]
        and crop_box[2] >= letter_box[2]
        and crop_box[3] >= letter_box[3]
    ):
        raise ValueError(f"volunteer_sample_crop_box_must_contain_letter_box:{sample_id}")
    crop_sha256 = _digest(_require(raw, "crop_sha256", "sample"), "sample_crop_sha256")
    crop_path = _text(_require(raw, "crop_path", "sample"), "sample_crop_path")
    if crop_path != f"crops/images/{crop_sha256}.png":
        raise ValueError(f"volunteer_sample_crop_not_content_addressed:{sample_id}")
    review_status = _text(_require(raw, "review_status", "sample"), "sample_review_status")
    if review_status not in REVIEW_STATUSES:
        raise ValueError(f"volunteer_sample_review_status_unknown:{sample_id}")
    crop_version = _text(
        _require(raw, "crop_generation_version", "sample"), "sample_crop_generation_version"
    )
    if crop_version != CROP_GENERATION_VERSION:
        raise ValueError(f"volunteer_sample_crop_version_changed:{sample_id}")
    return ManifestSample(
        sample_id=sample_id,
        writer_id=writer_id,
        group=group,
        truth=truth,
        letter_box=(letter_box[0], letter_box[1], letter_box[2], letter_box[3]),
        crop_box=(crop_box[0], crop_box[1], crop_box[2], crop_box[3]),
        crop_sha256=crop_sha256,
        crop_path=crop_path,
        source_sha256=_digest(_require(raw, "source_sha256", "sample"), "sample_source_sha256"),
        crop_generation_version=crop_version,
        review_status=review_status,
        crop_width=_positive_int(_require(raw, "crop_width", "sample"), "sample_crop_width"),
        crop_height=_positive_int(_require(raw, "crop_height", "sample"), "sample_crop_height"),
    )


def load_manifest(path: Path) -> Manifest:
    """Load and strictly validate a private crop manifest."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("volunteer_manifest_missing_or_unsafe")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("volunteer_manifest_must_be_an_object")
    if payload.get("version") != MANIFEST_VERSION:
        raise ValueError("volunteer_manifest_version_changed")
    raw_samples = _sequence(_require(payload, "samples", "manifest"), "samples")
    samples = tuple(_parse_sample(raw, index=index) for index, raw in enumerate(raw_samples))
    if len(samples) != EXPECTED_SAMPLES:
        raise ValueError("volunteer_manifest_sample_count_changed")
    if len({sample.sample_id for sample in samples}) != len(samples):
        raise ValueError("volunteer_manifest_duplicate_sample_id")
    by_writer: dict[str, list[ManifestSample]] = {writer: [] for writer in WRITERS}
    for sample in samples:
        by_writer[sample.writer_id].append(sample)
    for writer_id in WRITERS:
        ordered = by_writer.get(writer_id, [])
        expected = [expected_sample_id(writer_id, "upper", label) for label in UPPER]
        expected += [expected_sample_id(writer_id, "lower", label) for label in LOWER]
        if [sample.sample_id for sample in ordered] != expected:
            raise ValueError(f"volunteer_manifest_writer_inventory_changed:{writer_id}")
        source_hashes = {sample.source_sha256 for sample in ordered}
        if len(source_hashes) != 1:
            raise ValueError(f"volunteer_manifest_writer_uses_multiple_sources:{writer_id}")
    per_writer_source = {sample.writer_id: sample.source_sha256 for sample in samples}
    if len(set(per_writer_source.values())) != len(WRITERS):
        raise ValueError("volunteer_manifest_writers_share_a_source")

    raw_sources = _sequence(
        _require(payload, "sources", "manifest"), "sources", length=len(WRITERS)
    )
    sources: list[dict[str, Any]] = []
    for writer_id, raw in zip(WRITERS, raw_sources, strict=True):
        if not isinstance(raw, Mapping):
            raise ValueError("volunteer_manifest_source_must_be_an_object")
        recorded_writer = _require(raw, "writer_id", "source")
        if recorded_writer != writer_id:
            raise ValueError("volunteer_manifest_source_order_changed")
        record = {
            "writer_id": writer_id,
            "source_path": _text(_require(raw, "source_path", "source"), "source_path"),
            "source_sha256": _digest(_require(raw, "source_sha256", "source"), "source_sha256"),
            "source_bytes": _positive_int(
                _require(raw, "source_bytes", "source"),
                "source_bytes",
                maximum=64 * 1024 * 1024,
            ),
            "copied_path": _text(_require(raw, "copied_path", "source"), "source_copy_path"),
            "page_width": _positive_int(_require(raw, "page_width", "source"), "source_page_width"),
            "page_height": _positive_int(
                _require(raw, "page_height", "source"), "source_page_height"
            ),
        }
        if record["source_sha256"] != per_writer_source[writer_id]:
            raise ValueError(f"volunteer_manifest_source_hash_mismatch:{writer_id}")
        sources.append(record)

    generation = _require(payload, "crop_generation", "manifest")
    if not isinstance(generation, Mapping):
        raise ValueError("volunteer_crop_generation_must_be_an_object")
    crop_generation = {
        "version": _text(_require(generation, "version", "crop_generation"), "crop_version"),
        "margin_fraction": float(_require(generation, "margin_fraction", "crop_generation")),
        "square_padding": _text(
            _require(generation, "square_padding", "crop_generation"), "square_padding"
        ),
        "output_side": _positive_int(
            _require(generation, "output_side", "crop_generation"), "crop_output_side", maximum=4096
        ),
        "resampling": _text(_require(generation, "resampling", "crop_generation"), "resampling"),
        "contrast_adjustment": _text(
            _require(generation, "contrast_adjustment", "crop_generation"), "contrast_adjustment"
        ),
        "line_erasure": _text(
            _require(generation, "line_erasure", "crop_generation"), "line_erasure"
        ),
    }
    if crop_generation["version"] != CROP_GENERATION_VERSION:
        raise ValueError("volunteer_crop_generation_version_changed")

    excluded_raw = _sequence(_require(payload, "excluded", "manifest"), "excluded")
    included = _require(payload, "included_samples", "manifest")
    if included != len(samples):
        raise ValueError("volunteer_manifest_included_count_mismatch")
    if len(samples) + len(excluded_raw) != EXPECTED_SAMPLES:
        raise ValueError("volunteer_manifest_accounts_for_the_wrong_inventory")
    excluded: list[dict[str, Any]] = []
    for raw in excluded_raw:
        if not isinstance(raw, Mapping):
            raise ValueError("volunteer_exclusion_must_be_an_object")
        excluded.append(
            {
                "writer_id": _text(_require(raw, "writer_id", "exclusion"), "exclusion_writer"),
                "truth": _text(_require(raw, "truth", "exclusion"), "exclusion_truth", limit=1),
                "reason": _text(_require(raw, "reason", "exclusion"), "exclusion_reason"),
            }
        )
    return Manifest(
        path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        samples=samples,
        sources=tuple(sources),
        crop_generation=crop_generation,
        excluded=tuple(excluded),
    )
