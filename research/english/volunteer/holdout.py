"""Separate W06–W09 external holdout: import, scoring and paired comparison.

Nothing here invents data. Until the four pages and their reviewed boxes are supplied the
module refuses with ``volunteer_holdout_w06_w09_data_not_available``. When they arrive:

* :func:`prepare` freezes the writer roles *before* any model evaluation, copies the page
  bytes, cuts the same A–Z/a–z crops with the same crop contract as the pilot, writes a
  private holdout manifest and contact sheets, and refuses a registry whose writers were
  used for development or training;
* :func:`compare` joins two saved prediction files by exact sample ID — the frozen R6
  baseline and the adapted candidate — and reports paired fixes, regressions and the
  small-external-pilot warning. It runs no model.

The holdout is never used to select preprocessing, tune the adaptation recipe or choose a
pass. Its role is frozen and checked by digest.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from research.english.volunteer.manifest import LOWER, UPPER, ManifestSample
from research.english.volunteer.manifest import WRITERS as DEVELOPMENT_WRITERS
from research.english.volunteer.prepare import (
    CONTACT_SHEET_DIR,
    CROP_DIR,
    CROP_GENERATION_VERSION,
    CROP_SIDE,
    MANIFEST_VERSION,
    MARGIN_FRACTION,
    RESAMPLING,
    SOURCE_DIR,
    SQUARE_PADDING,
    contact_sheet,
    crop_letter,
    private_run_directory,
)
from research.pretrained.artifacts import digest_file

HOLDOUT_WRITERS: tuple[str, ...] = ("W06", "W07", "W08", "W09")
HOLDOUT_ROLE = "external_holdout_writers"
REGISTRY_VERSION = "penforge-volunteer-holdout-coordinates-v1"
PARTIAL_REGISTRY_VERSION = "penforge-volunteer-holdout-coordinates-v2"
ROLES_VERSION = "penforge-volunteer-holdout-roles-v1"
HOLDOUT_MANIFEST_VERSION = "penforge-volunteer-holdout-manifest-v1"
PARTIAL_MANIFEST_VERSION = "penforge-volunteer-holdout-manifest-v2"
PREPARED_VERSION = "penforge-volunteer-holdout-prepared-v1"
PARTIAL_PREPARED_VERSION = "penforge-volunteer-holdout-prepared-v2"
COORDINATES_NAME = "coordinates-frozen.json"
COMPARISON_VERSION = "penforge-volunteer-holdout-comparison-v1"
ROLES_NAME = "roles-frozen.json"
MANIFEST_NAME = "holdout-manifest.json"
PREPARED_NAME = "prepared.json"
COMPARISON_NAME = "holdout-comparison.json"
PREDICTION_VERSION = "penforge-volunteer-predictions-v1"
EXPECTED_PER_WRITER = len(UPPER) + len(LOWER)
EXPECTED_SAMPLES = EXPECTED_PER_WRITER * len(HOLDOUT_WRITERS)
MAX_SOURCE_BYTES = 64 * 1024 * 1024
LABEL = "small_external_pilot_with_high_uncertainty"
UNAVAILABLE = "volunteer_holdout_w06_w09_data_not_available"
_HEX = set("0123456789abcdef")


class HoldoutUnavailable(ValueError):
    """The four holdout pages have not been supplied yet."""


def _require(mapping: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        raise ValueError(f"volunteer_holdout_{label}_missing_field:{key}")
    return mapping[key]


def _text(value: object, field: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise ValueError(f"volunteer_holdout_{field}_must_be_bounded_text")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX:
        raise ValueError(f"volunteer_holdout_{field}_must_be_lowercase_sha256")
    return value


def _integer(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"volunteer_holdout_{field}_must_be_a_bounded_integer")
    return value


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("wb") as stream:
        stream.write(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _write_new_or_identical(root: Path, relative: str, payload: bytes) -> bool:
    target = root / relative
    if target.is_symlink():
        raise ValueError(f"volunteer_holdout_output_must_not_be_symlink:{relative}")
    if target.exists():
        if target.read_bytes() != payload:
            raise FileExistsError(f"volunteer_holdout_existing_output_differs:{relative}")
        return False
    _atomic_write(target, payload)
    return True


def _read_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"volunteer_holdout_{label}_missing_or_unsafe")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class HoldoutBox:
    label: str
    group: str
    x0: int
    y0: int
    x1: int
    y1: int


@dataclass(frozen=True)
class HoldoutWriter:
    writer_id: str
    source_path: str
    source_sha256: str
    source_bytes: int
    page_width: int
    page_height: int
    boxes: tuple[HoldoutBox, ...]
    excluded: tuple[dict[str, str], ...] = ()


def _validate_inventory(
    boxes: Sequence[HoldoutBox], excluded: object, *, writer_id: str
) -> tuple[dict[str, str], ...]:
    """Account for all 52 cases without inventing observations for missing letters."""
    labels = [box.label for box in boxes]
    canonical = UPPER + LOWER
    if not labels or len(set(labels)) != len(labels):
        raise ValueError(f"volunteer_holdout_duplicate_or_empty_inventory:{writer_id}")
    if labels != [letter for letter in canonical if letter in labels]:
        raise ValueError(f"volunteer_holdout_label_order_changed:{writer_id}")
    for index, box in enumerate(boxes):
        for other in boxes[index + 1 :]:
            if max(box.x0, other.x0) < min(box.x1, other.x1) and max(box.y0, other.y0) < min(
                box.y1, other.y1
            ):
                raise ValueError(f"volunteer_holdout_overlapping_boxes:{writer_id}")
    if not isinstance(excluded, list):
        raise ValueError(f"volunteer_holdout_exclusions_required:{writer_id}")
    records: list[dict[str, str]] = []
    for item in excluded:
        if not isinstance(item, Mapping):
            raise ValueError("volunteer_holdout_exclusion_must_be_an_object")
        label = item.get("label")
        reason = item.get("reason")
        if not isinstance(label, str) or len(label) != 1 or label not in canonical:
            raise ValueError("volunteer_holdout_exclusion_label_invalid")
        if not isinstance(reason, str) or reason not in {
            "absent_on_page",
            "ambiguous_case",
            "unclear_or_clipped_glyph",
        }:
            raise ValueError("volunteer_holdout_exclusion_reason_invalid")
        records.append({"label": label, "reason": str(reason)})
    omitted = [record["label"] for record in records]
    if omitted != [letter for letter in canonical if letter not in labels]:
        raise ValueError(f"volunteer_holdout_exclusions_must_account_for_missing_cases:{writer_id}")
    return tuple(records)


def _parse_box(raw: object, *, writer_id: str, width: int, height: int) -> HoldoutBox:
    if not isinstance(raw, Mapping):
        raise ValueError("volunteer_holdout_box_must_be_an_object")
    label = _text(_require(raw, "label", "box"), "box_label", limit=1)
    group = _text(_require(raw, "group", "box"), "box_group", limit=8)
    if group not in {"upper", "lower"}:
        raise ValueError("volunteer_holdout_box_group_must_be_upper_or_lower")
    if label not in (UPPER if group == "upper" else LOWER):
        raise ValueError(f"volunteer_holdout_box_label_case_mismatch:{writer_id}:{label}")
    x0 = _integer(_require(raw, "x0", "box"), "box_x0", minimum=0, maximum=width)
    y0 = _integer(_require(raw, "y0", "box"), "box_y0", minimum=0, maximum=height)
    x1 = _integer(_require(raw, "x1", "box"), "box_x1", minimum=1, maximum=width)
    y1 = _integer(_require(raw, "y1", "box"), "box_y1", minimum=1, maximum=height)
    if not (x0 < x1 and y0 < y1 and x1 - x0 >= 4 and y1 - y0 >= 4):
        raise ValueError(f"volunteer_holdout_box_degenerate:{writer_id}:{label}")
    return HoldoutBox(label=label, group=group, x0=x0, y0=y0, x1=x1, y1=y1)


def validate_registry(payload: object) -> tuple[HoldoutWriter, ...]:
    """Validate full v1 pages or exhaustive observed/excluded case inventories in v2."""
    if not isinstance(payload, Mapping):
        raise ValueError("volunteer_holdout_registry_must_be_an_object")
    partial = payload.get("version") == PARTIAL_REGISTRY_VERSION
    if payload.get("version") not in {REGISTRY_VERSION, PARTIAL_REGISTRY_VERSION}:
        raise ValueError("volunteer_holdout_registry_version_changed")
    if payload.get("role") != HOLDOUT_ROLE:
        raise ValueError("volunteer_holdout_registry_role_changed")
    for flag in (
        "frozen_before_model_evaluation",
        "excluded_from_training",
        "excluded_from_preprocessing_selection",
    ):
        if payload.get(flag) is not True:
            raise ValueError(f"volunteer_holdout_registry_requires_{flag}")
    raw_writers = _require(payload, "writers", "registry")
    if not isinstance(raw_writers, list) or len(raw_writers) != len(HOLDOUT_WRITERS):
        raise ValueError("volunteer_holdout_registry_must_declare_w06_to_w09")
    writers: list[HoldoutWriter] = []
    for position, (writer_id, raw) in enumerate(zip(HOLDOUT_WRITERS, raw_writers, strict=True)):
        if not isinstance(raw, Mapping):
            raise ValueError("volunteer_holdout_writer_must_be_an_object")
        if _require(raw, "writer_id", "writer") != writer_id:
            raise ValueError(f"volunteer_holdout_writer_order_changed:{position}")
        source_path = _text(_require(raw, "source_path", "writer"), "source_path")
        if not Path(source_path).is_absolute():
            raise ValueError(f"volunteer_holdout_source_path_must_be_absolute:{writer_id}")
        source_bytes = _integer(
            _require(raw, "source_bytes", "writer"),
            "source_bytes",
            minimum=1,
            maximum=MAX_SOURCE_BYTES,
        )
        page_width = _integer(
            _require(raw, "page_width", "writer"), "page_width", minimum=32, maximum=100_000
        )
        page_height = _integer(
            _require(raw, "page_height", "writer"), "page_height", minimum=32, maximum=100_000
        )
        raw_boxes = _require(raw, "boxes", "writer")
        if (
            not isinstance(raw_boxes, list)
            or (not partial and len(raw_boxes) != EXPECTED_PER_WRITER)
            or (partial and not 1 <= len(raw_boxes) <= EXPECTED_PER_WRITER)
        ):
            raise ValueError(f"volunteer_holdout_writer_must_declare_52_boxes:{writer_id}")
        boxes = tuple(
            _parse_box(box, writer_id=writer_id, width=page_width, height=page_height)
            for box in raw_boxes
        )
        excluded = (
            _validate_inventory(boxes, raw.get("excluded"), writer_id=writer_id) if partial else ()
        )
        if not partial and "".join(box.label for box in boxes) != UPPER + LOWER:
            raise ValueError(f"volunteer_holdout_label_order_changed:{writer_id}")
        if not partial and tuple(box.group for box in boxes) != ("upper",) * 26 + ("lower",) * 26:
            raise ValueError(f"volunteer_holdout_group_order_changed:{writer_id}")
        writers.append(
            HoldoutWriter(
                writer_id=writer_id,
                source_path=source_path,
                source_sha256=_digest(_require(raw, "source_sha256", "writer"), "source_sha256"),
                source_bytes=source_bytes,
                page_width=page_width,
                page_height=page_height,
                boxes=boxes,
                excluded=excluded,
            )
        )
    hashes = [writer.source_sha256 for writer in writers]
    paths = [writer.source_path for writer in writers]
    if len(set(hashes)) != len(hashes) or len(set(paths)) != len(paths):
        raise ValueError("volunteer_holdout_writers_must_be_distinct_pages")
    if set(HOLDOUT_WRITERS) & set(DEVELOPMENT_WRITERS):
        raise ValueError("volunteer_holdout_writers_overlap_the_development_writers")
    return tuple(writers)


def expected_sample_id(writer_id: str, group: str, label: str) -> str:
    return f"{writer_id}-{group}-{label}"


def _source_copy_name(writer: HoldoutWriter) -> str:
    suffix = Path(writer.source_path).suffix.lower() or ".img"
    if suffix not in {".jpeg", ".jpg", ".png", ".webp"}:
        suffix = ".img"
    return f"{SOURCE_DIR}/{writer.writer_id}{suffix}"


def prepare(coordinates: Path, run: Path) -> dict[str, Any]:
    """Import W06–W09 and freeze their holdout role. Data only; no model."""
    source = Path(coordinates)
    if source.is_symlink() or not source.is_file():
        raise HoldoutUnavailable(UNAVAILABLE)
    registry_bytes = source.read_bytes()
    registry = json.loads(registry_bytes)
    writers = validate_registry(registry)
    partial = registry["version"] == PARTIAL_REGISTRY_VERSION
    root = private_run_directory(run)
    if (root / ROLES_NAME).exists():
        previous = _read_json(root / ROLES_NAME, "roles")
        if previous.get("coordinates_sha256") != hashlib.sha256(registry_bytes).hexdigest():
            raise FileExistsError("volunteer_holdout_coordinates_already_frozen")
    outputs: dict[str, bytes] = {}
    samples: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    sheet_crops: dict[str, dict[str, Image.Image]] = {writer.writer_id: {} for writer in writers}
    for writer in writers:
        raw = Path(writer.source_path)
        if raw.is_symlink() or not raw.is_file():
            raise ValueError(f"volunteer_holdout_source_missing:{writer.writer_id}")
        payload = raw.read_bytes()
        if len(payload) != writer.source_bytes:
            raise ValueError(f"volunteer_holdout_source_size_changed:{writer.writer_id}")
        if hashlib.sha256(payload).hexdigest() != writer.source_sha256:
            raise ValueError(f"volunteer_holdout_source_hash_changed:{writer.writer_id}")
        with Image.open(io.BytesIO(payload)) as probe:
            if probe.width != writer.page_width or probe.height != writer.page_height:
                raise ValueError(f"volunteer_holdout_source_dimensions_changed:{writer.writer_id}")
            page = probe.convert("RGB")
        copy_name = _source_copy_name(writer)
        outputs[copy_name] = payload
        sources.append(
            {
                "writer_id": writer.writer_id,
                "source_path": writer.source_path,
                "source_sha256": writer.source_sha256,
                "source_bytes": writer.source_bytes,
                "copied_path": copy_name,
                "page_width": writer.page_width,
                "page_height": writer.page_height,
            }
        )
        for box in writer.boxes:
            crop, margin = crop_letter(page, (box.x0, box.y0, box.x1, box.y1))
            buffer = io.BytesIO()
            crop.save(buffer, format="PNG", optimize=False)
            crop_bytes = buffer.getvalue()
            digest = hashlib.sha256(crop_bytes).hexdigest()
            outputs[f"{CROP_DIR}/{digest}.png"] = crop_bytes
            sample_id = expected_sample_id(writer.writer_id, box.group, box.label)
            samples.append(
                {
                    "sample_id": sample_id,
                    "writer_id": writer.writer_id,
                    "group": box.group,
                    "truth": box.label,
                    "letter_box": [box.x0, box.y0, box.x1, box.y1],
                    "crop_box": [
                        max(0, box.x0 - margin),
                        max(0, box.y0 - margin),
                        min(page.width, box.x1 + margin),
                        min(page.height, box.y1 + margin),
                    ],
                    "crop_width": crop.width,
                    "crop_height": crop.height,
                    "crop_sha256": digest,
                    "crop_path": f"{CROP_DIR}/{digest}.png",
                    "source_sha256": writer.source_sha256,
                    "crop_generation_version": CROP_GENERATION_VERSION,
                    "review_status": "reviewed_agent",
                    "role": HOLDOUT_ROLE,
                }
            )
            sheet_crops[writer.writer_id][sample_id] = crop
    if len(samples) != sum(len(writer.boxes) for writer in writers):
        raise ValueError("volunteer_holdout_crop_count_differs_from_inventory")
    order = [
        expected_sample_id(writer.writer_id, group, label)
        for writer in writers
        for box in writer.boxes
        for group, label in ((box.group, box.label),)
    ]
    by_id = {sample["sample_id"]: sample for sample in samples}
    if sorted(order) != sorted(by_id):
        raise ValueError("volunteer_holdout_inventory_changed")
    samples = [by_id[sample_id] for sample_id in order]
    manifest = {
        "version": PARTIAL_MANIFEST_VERSION if partial else HOLDOUT_MANIFEST_VERSION,
        "role": HOLDOUT_ROLE,
        "scope": "isolated_uppercase_and_lowercase_letter_crops_only",
        "crop_manifest_version": MANIFEST_VERSION,
        "crop_generation": {
            "version": CROP_GENERATION_VERSION,
            "margin_fraction": MARGIN_FRACTION,
            "square_padding": SQUARE_PADDING,
            "output_side": CROP_SIDE,
            "resampling": RESAMPLING,
            "contrast_adjustment": "none_original_page_pixels",
            "line_erasure": "none",
        },
        "sources": sources,
        "included_samples": len(samples),
        "excluded": [
            {"writer_id": writer.writer_id, **item}
            for writer in writers
            for item in writer.excluded
        ],
        "samples": samples,
    }
    manifest_bytes = _json_bytes(manifest)
    outputs[MANIFEST_NAME] = manifest_bytes
    for writer in writers:
        outputs[f"{CONTACT_SHEET_DIR}/{writer.writer_id}.png"] = contact_sheet(
            [
                _manifest_view(sample)
                for sample in samples
                if sample["writer_id"] == writer.writer_id
            ],
            sheet_crops[writer.writer_id],
            writer_id=writer.writer_id,
            context=(
                "Four-writer external pilot. Only reviewed observed cases are scored; omissions are explicit."
                if partial
                else None
            ),
        )
    roles = {
        "version": ROLES_VERSION,
        "role": HOLDOUT_ROLE,
        "writers": list(HOLDOUT_WRITERS),
        "writer_roles_frozen": True,
        "frozen_before_model_evaluation": True,
        "excluded_from_training": True,
        "excluded_from_preprocessing_selection": True,
        "coordinates_sha256": hashlib.sha256(registry_bytes).hexdigest(),
        "crop_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "model_execution": False,
        "training_performed": False,
    }
    outputs[ROLES_NAME] = _json_bytes(roles)
    if partial:
        outputs[COORDINATES_NAME] = registry_bytes
    outputs[PREPARED_NAME] = _json_bytes(
        {
            "version": PARTIAL_PREPARED_VERSION if partial else PREPARED_VERSION,
            "status": "prepared_awaiting_owner_evaluation",
            "role": HOLDOUT_ROLE,
            "writers": list(HOLDOUT_WRITERS),
            "included_samples": len(samples),
            "expected_samples": EXPECTED_SAMPLES,
            "crop_manifest_sha256": roles["crop_manifest_sha256"],
            "roles_sha256": hashlib.sha256(outputs[ROLES_NAME]).hexdigest(),
            "model_execution": False,
            "training_performed": False,
            "next_stage": "owner_evaluate_frozen_baseline_and_adapted_candidate_then_compare",
        }
    )
    written = 0
    # Validate every existing target before publishing any new file.
    for relative, payload in outputs.items():
        target = root / relative
        if target.is_symlink() or (target.exists() and target.read_bytes() != payload):
            raise FileExistsError(f"volunteer_holdout_existing_output_differs:{relative}")
    for relative, payload in sorted(outputs.items()):
        written += int(_write_new_or_identical(root, relative, payload))
    return {
        "status": "prepared_awaiting_owner_evaluation" if written else "verified_existing_holdout",
        "run": str(root),
        "writers": list(HOLDOUT_WRITERS),
        "included_samples": len(samples),
        "files_written": written,
        "role": HOLDOUT_ROLE,
        "excluded_from_training": True,
        "model_execution": False,
        "training_performed": False,
    }


def _manifest_view(sample: Mapping[str, Any]) -> ManifestSample:
    return ManifestSample(
        sample_id=str(sample["sample_id"]),
        writer_id=str(sample["writer_id"]),
        group=str(sample["group"]),
        truth=str(sample["truth"]),
        letter_box=(
            int(sample["letter_box"][0]),
            int(sample["letter_box"][1]),
            int(sample["letter_box"][2]),
            int(sample["letter_box"][3]),
        ),
        crop_box=(
            int(sample["crop_box"][0]),
            int(sample["crop_box"][1]),
            int(sample["crop_box"][2]),
            int(sample["crop_box"][3]),
        ),
        crop_sha256=str(sample["crop_sha256"]),
        crop_path=str(sample["crop_path"]),
        source_sha256=str(sample["source_sha256"]),
        crop_generation_version=str(sample["crop_generation_version"]),
        review_status=str(sample["review_status"]),
        crop_width=int(sample["crop_width"]),
        crop_height=int(sample["crop_height"]),
    )


def load_prepared(run: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Verify the frozen holdout before use; refuse when W06–W09 are absent."""
    root = private_run_directory(run)
    if not (root / PREPARED_NAME).is_file():
        raise HoldoutUnavailable(UNAVAILABLE)
    prepared = _read_json(root / PREPARED_NAME, "prepared")
    partial = prepared.get("version") == PARTIAL_PREPARED_VERSION
    if prepared.get("version") not in {PREPARED_VERSION, PARTIAL_PREPARED_VERSION}:
        raise ValueError("volunteer_holdout_prepared_version_changed")
    if prepared.get("status") != "prepared_awaiting_owner_evaluation":
        raise ValueError("volunteer_holdout_prepared_status_changed")
    if prepared.get("role") != HOLDOUT_ROLE:
        raise ValueError("volunteer_holdout_prepared_role_changed")
    if prepared.get("model_execution") is not False:
        raise ValueError("volunteer_holdout_prepared_claims_model_work")
    roles = _read_json(root / ROLES_NAME, "roles")
    if (
        roles.get("version") != ROLES_VERSION
        or roles.get("role") != HOLDOUT_ROLE
        or roles.get("writer_roles_frozen") is not True
        or roles.get("frozen_before_model_evaluation") is not True
        or roles.get("excluded_from_training") is not True
        or roles.get("excluded_from_preprocessing_selection") is not True
        or list(roles.get("writers", [])) != list(HOLDOUT_WRITERS)
    ):
        raise ValueError("volunteer_holdout_role_freeze_changed")
    manifest = _read_json(root / MANIFEST_NAME, "manifest")
    expected_version = PARTIAL_MANIFEST_VERSION if partial else HOLDOUT_MANIFEST_VERSION
    if manifest.get("version") != expected_version:
        raise ValueError("volunteer_holdout_manifest_version_changed")
    if manifest.get("role") != HOLDOUT_ROLE:
        raise ValueError("volunteer_holdout_manifest_role_changed")
    samples = manifest.get("samples")
    count = prepared.get("included_samples")
    if (
        not isinstance(samples, list)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or len(samples) != count
        or (not partial and count != EXPECTED_SAMPLES)
        or not 1 <= count <= EXPECTED_SAMPLES
        or prepared.get("crop_manifest_sha256") != roles.get("crop_manifest_sha256")
    ):
        raise ValueError("volunteer_holdout_sample_count_changed")
    if digest_file(root / MANIFEST_NAME) != roles.get("crop_manifest_sha256"):
        raise ValueError("volunteer_holdout_manifest_changed_after_freeze")
    if digest_file(root / ROLES_NAME) != prepared.get("roles_sha256"):
        raise ValueError("volunteer_holdout_roles_changed_after_preparation")
    writers = None
    if partial:
        registry_path = root / COORDINATES_NAME
        registry = _read_json(registry_path, "coordinates")
        if digest_file(registry_path) != roles.get("coordinates_sha256"):
            raise ValueError("volunteer_holdout_coordinates_changed_after_freeze")
        writers = validate_registry(registry)
        if registry.get("version") != PARTIAL_REGISTRY_VERSION:
            raise ValueError("volunteer_holdout_partial_registry_version_changed")
        expected_excluded = [
            {"writer_id": writer.writer_id, **item}
            for writer in writers
            for item in writer.excluded
        ]
        if manifest.get("excluded") != expected_excluded:
            raise ValueError("volunteer_holdout_exclusions_changed")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or [item.get("writer_id") for item in sources] != list(
        HOLDOUT_WRITERS
    ):
        raise ValueError("volunteer_holdout_source_inventory_changed")
    for source in sources:
        if writers is not None:
            writer = next(item for item in writers if item.writer_id == source["writer_id"])
            if (
                source.get("source_sha256") != writer.source_sha256
                or source.get("source_bytes") != writer.source_bytes
                or source.get("copied_path") != _source_copy_name(writer)
            ):
                raise ValueError("volunteer_holdout_source_differs_from_registry")
        source_path = root / str(source.get("copied_path"))
        if (
            source_path.is_symlink()
            or not source_path.is_file()
            or not source_path.resolve().is_relative_to(root)
        ):
            raise ValueError("volunteer_holdout_source_missing_or_unsafe")
        if digest_file(source_path) != source.get("source_sha256"):
            raise ValueError("volunteer_holdout_source_changed")
    by_writer: dict[str, list[Mapping[str, Any]]] = {writer: [] for writer in HOLDOUT_WRITERS}
    for sample in samples:
        writer_id = sample.get("writer_id")
        if writer_id not in by_writer:
            raise ValueError("volunteer_holdout_sample_names_an_unknown_writer")
        if sample.get("role") != HOLDOUT_ROLE:
            raise ValueError("volunteer_holdout_sample_role_changed")
        if sample.get("sample_id") != expected_sample_id(
            writer_id, str(sample.get("group")), str(sample.get("truth"))
        ):
            raise ValueError("volunteer_holdout_sample_id_changed")
        group, truth = sample.get("group"), sample.get("truth")
        if (
            group not in {"upper", "lower"}
            or not isinstance(truth, str)
            or len(truth) != 1
            or truth not in (UPPER if group == "upper" else LOWER)
        ):
            raise ValueError("volunteer_holdout_sample_label_case_changed")
        source = next(item for item in sources if item["writer_id"] == writer_id)
        digest = _digest(sample.get("crop_sha256"), "crop_sha256")
        if (
            sample.get("source_sha256") != source["source_sha256"]
            or sample.get("crop_path") != f"{CROP_DIR}/{digest}.png"
            or sample.get("crop_generation_version") != CROP_GENERATION_VERSION
        ):
            raise ValueError("volunteer_holdout_crop_provenance_changed")
        path = root / str(sample.get("crop_path"))
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError(f"volunteer_holdout_crop_missing:{sample.get('sample_id')}")
        if digest_file(path) != sample.get("crop_sha256"):
            raise ValueError(f"volunteer_holdout_crop_changed:{sample.get('sample_id')}")
        by_writer[writer_id].append(sample)
    for writer_id, values in by_writer.items():
        if writers is not None:
            writer = next(item for item in writers if item.writer_id == writer_id)
            expected = [expected_sample_id(writer_id, box.group, box.label) for box in writer.boxes]
            if [value["letter_box"] for value in values] != [
                [box.x0, box.y0, box.x1, box.y1] for box in writer.boxes
            ]:
                raise ValueError("volunteer_holdout_sample_boxes_changed")
        else:
            expected = [expected_sample_id(writer_id, "upper", label) for label in UPPER]
            expected += [expected_sample_id(writer_id, "lower", label) for label in LOWER]
        if [value["sample_id"] for value in values] != expected:
            raise ValueError(f"volunteer_holdout_writer_inventory_changed:{writer_id}")
    if [sample["sample_id"] for sample in samples] != [
        value["sample_id"] for writer in HOLDOUT_WRITERS for value in by_writer[writer]
    ]:
        raise ValueError("volunteer_holdout_reading_order_changed")
    return root, prepared, manifest


def load_predictions(path: Path) -> dict[str, dict[str, Any]]:
    """Read saved raw predictions for one candidate; strip nothing, change nothing."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("volunteer_holdout_predictions_missing_or_unsafe")
    records: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"volunteer_holdout_predictions_blank_line:{number}")
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"volunteer_holdout_predictions_row_shape:{number}")
        sample_id = value.get("sample_id")
        prediction = value.get("raw_prediction", value.get("prediction"))
        if not isinstance(sample_id, str) or not isinstance(prediction, str):
            raise ValueError(f"volunteer_holdout_predictions_row_shape:{number}")
        if sample_id in records:
            raise ValueError(f"volunteer_holdout_predictions_duplicate:{sample_id}")
        records[sample_id] = {
            "sample_id": sample_id,
            "writer_id": value.get("writer_id"),
            "truth": value.get("truth"),
            "prediction": prediction,
        }
    return records


def _summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    correct = sum(record["truth"] == record["prediction"] for record in records)
    folded = sum(
        len(record["prediction"]) == 1
        and record["prediction"].casefold() == record["truth"].casefold()
        for record in records
    )
    return {
        "samples": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "casefolded_correct": folded,
        "casefolded_shape_accuracy": folded / total if total else None,
        "case_only_errors": folded - correct,
        "empty_predictions": sum(not record["prediction"].strip() for record in records),
    }


def _records(
    manifest: Mapping[str, Any], predictions: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    samples = manifest.get("samples")
    if not isinstance(samples, list):
        raise ValueError("volunteer_holdout_manifest_samples_missing")
    expected = {str(sample["sample_id"]): sample for sample in samples}
    if set(predictions) != set(expected):
        raise ValueError("volunteer_holdout_prediction_inventory_changed")
    records = []
    for sample_id, sample in expected.items():
        record = predictions[sample_id]
        if record["truth"] != sample["truth"] or record["writer_id"] != sample["writer_id"]:
            raise ValueError(f"volunteer_holdout_prediction_truth_or_writer_changed:{sample_id}")
        records.append(
            {
                "sample_id": sample_id,
                "writer_id": sample["writer_id"],
                "group": sample["group"],
                "truth": sample["truth"],
                "prediction": record["prediction"],
            }
        )
    return records


def score(
    manifest: Mapping[str, Any], predictions: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Exact and folded metrics over the four holdout writers; no uncertainty claim."""
    records = _records(manifest, predictions)
    overall = _summary(records)
    return {
        **overall,
        "total": overall["samples"],
        "by_writer": {
            writer: _summary([record for record in records if record["writer_id"] == writer])
            for writer in HOLDOUT_WRITERS
        },
        "by_case": {
            "uppercase": _summary([record for record in records if record["group"] == "upper"]),
            "lowercase": _summary([record for record in records if record["group"] == "lower"]),
        },
        "accuracy_definition": "exact_case_sensitive_single_letter_match",
        "normalization": "none_raw_exact_no_trimming_no_case_change_no_language_correction",
        "uncertainty": "four_writers_too_few_for_resampling_based_uncertainty",
        "label": LABEL,
    }


def paired(
    baseline: Mapping[str, Mapping[str, Any]], adapted: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Paired fixes and regressions by exact sample ID."""
    if set(baseline) != set(adapted):
        raise ValueError("volunteer_holdout_paired_inventory_changed")
    fixes: list[str] = []
    regressions: list[str] = []
    unchanged_errors: list[str] = []
    for sample_id, before in baseline.items():
        after = adapted[sample_id]
        truth = before["truth"]
        was_correct = before["prediction"] == truth
        now_correct = after["prediction"] == truth
        if now_correct and not was_correct:
            fixes.append(sample_id)
        elif was_correct and not now_correct:
            regressions.append(sample_id)
        elif not was_correct and not now_correct:
            unchanged_errors.append(sample_id)
    return {
        "fixes": sorted(fixes),
        "regressions": sorted(regressions),
        "unchanged_errors": sorted(unchanged_errors),
        "fix_count": len(fixes),
        "regression_count": len(regressions),
        "unchanged_error_count": len(unchanged_errors),
        "net_change": len(fixes) - len(regressions),
    }


def compare(
    run: Path,
    *,
    baseline_predictions: Path,
    adapted_predictions: Path,
) -> dict[str, Any]:
    """Owner-run: join two saved holdout prediction files and write the paired report."""
    root, prepared, manifest = load_prepared(run)
    baseline = load_predictions(baseline_predictions)
    adapted = load_predictions(adapted_predictions)
    report = {
        "version": COMPARISON_VERSION,
        "status": "owner_run_external_holdout_comparison",
        "role": HOLDOUT_ROLE,
        "label": LABEL,
        "scope": "isolated_uppercase_and_lowercase_letter_crops_only",
        "writers": list(HOLDOUT_WRITERS),
        "samples": len(manifest["samples"]),
        "excluded_cases": len(manifest.get("excluded", [])),
        "holdout": {
            "run": str(root),
            "crop_manifest_sha256": prepared.get("crop_manifest_sha256"),
            "roles_sha256": prepared.get("roles_sha256"),
            "frozen_before_model_evaluation": True,
            "excluded_from_training": True,
            "excluded_from_preprocessing_selection": True,
        },
        "baseline": {
            "samples": score(manifest, baseline),
            "predictions_sha256": digest_file(Path(baseline_predictions)),
        },
        "adapted": {
            "samples": score(manifest, adapted),
            "predictions_sha256": digest_file(Path(adapted_predictions)),
        },
        "paired": paired(baseline, adapted),
        "interpretation": (
            "Four volunteer writers are a small external pilot. Report it as paired "
            "evidence with high uncertainty, never as a product claim, a confidence "
            "interval or a generalisation guarantee."
        ),
        "product_qualification": "none",
        "model_execution": False,
        "training_performed": False,
    }
    target = root / COMPARISON_NAME
    if target.exists():
        if target.read_bytes() != _json_bytes(report):
            raise FileExistsError("volunteer_holdout_comparison_already_exists")
    else:
        _atomic_write(target, _json_bytes(report))
    return report


def summary(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": value["status"],
        "label": value["label"],
        "writers": value["writers"],
        "samples": value["samples"],
        "excluded_cases": value.get("excluded_cases", 0),
        "baseline_accuracy": value["baseline"]["samples"]["accuracy"],
        "baseline_correct": value["baseline"]["samples"]["correct"],
        "adapted_accuracy": value["adapted"]["samples"]["accuracy"],
        "adapted_correct": value["adapted"]["samples"]["correct"],
        "fixes": value["paired"]["fix_count"],
        "regressions": value["paired"]["regression_count"],
        "unchanged_errors": value["paired"]["unchanged_error_count"],
        "per_writer": {
            writer: {
                "samples": value["baseline"]["samples"]["by_writer"][writer]["samples"],
                "baseline_accuracy": value["baseline"]["samples"]["by_writer"][writer]["accuracy"],
                "adapted_accuracy": value["adapted"]["samples"]["by_writer"][writer]["accuracy"],
            }
            for writer in HOLDOUT_WRITERS
        },
        "product_qualification": value["product_qualification"],
        "model_execution": False,
        "training_performed": False,
    }


def main() -> None:  # pragma: no cover - exercised through the owner wrapper
    import sys

    if len(sys.argv) < 3:
        print(
            "usage: python -m research.english.volunteer.holdout "
            "prepare <coordinates> <run> | compare <run> <baseline> <adapted>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    stage = sys.argv[1]
    try:
        if stage == "prepare" and len(sys.argv) == 4:
            value = prepare(Path(sys.argv[2]), Path(sys.argv[3]))
        elif stage == "compare" and len(sys.argv) == 5:
            value = summary(
                compare(
                    Path(sys.argv[2]),
                    baseline_predictions=Path(sys.argv[3]),
                    adapted_predictions=Path(sys.argv[4]),
                )
            )
        else:
            raise SystemExit(2)
    except (ValueError, FileNotFoundError, FileExistsError) as error:
        print(
            json.dumps({"status": "refused", "reason": str(error), "automatic_retry": False}),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    print(json.dumps(value, sort_keys=True, allow_nan=False))


__all__ = [
    "COMPARISON_NAME",
    "HOLDOUT_ROLE",
    "HOLDOUT_WRITERS",
    "HoldoutUnavailable",
    "LABEL",
    "MANIFEST_NAME",
    "PREPARED_NAME",
    "ROLES_NAME",
    "UNAVAILABLE",
    "compare",
    "expected_sample_id",
    "load_predictions",
    "load_prepared",
    "paired",
    "prepare",
    "score",
    "summary",
    "validate_registry",
]
