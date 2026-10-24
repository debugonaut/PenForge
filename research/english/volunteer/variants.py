"""Deterministic preprocessing variants over the frozen volunteer crops.

Five fixed conditions, one implementation each, one fixed parameter set for every writer
and all 260 crops. Nothing is tuned per image: every crop of a condition passes through
the same function with the same parameters, and the only per-image values are statistics
the algorithm itself computes (background illumination, contrast percentiles, ruled-line
rows). No crop is stretched, no stroke is invented and no label is touched.

* ``raw`` — a byte-identical copy of the baseline crop. It is the already measured
  control (50.77%) and is never regenerated from page pixels here.
* ``illumination-normalized`` — multiplicative flat-field correction against a large
  Gaussian background estimate, so uneven page lighting flattens while ink geometry is
  preserved.
* ``contrast-normalized`` — one linear stretch from fixed low/high luminance percentiles
  applied equally to all three channels, with a fixed no-op guard for nearly flat crops
  so faint strokes are not amplified into noise.
* ``ruled-line-aware`` — removes only notebook rules that reach both crop edges, then
  interpolates them away. It refuses (and returns the input unchanged) when removing
  them would take more than a fixed fraction of the ink.
* ``combined`` — illumination, then conservative rule handling, then square padding to
  the fixed canvas with the estimated paper colour.

Every derivative records its source crop hash, algorithm version and parameters in the
variant manifest. The original crops, the pilot manifest and the pilot run are read-only.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from research.english.volunteer.manifest import (
    EXPECTED_SAMPLES,
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

VARIANTS_VERSION = "penforge-volunteer-preprocess-manifest-v1"
PILOT_LINK_NAME = "pilot-link.json"
MANIFEST_NAME_OUT = "variant-manifest.json"
PREPARED_NAME = "prepared.json"
VARIANTS_DIR = "variants"
CANVAS = 384
INK_THRESHOLD = 110
MAX_CROP_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class VariantSpec:
    """One fixed condition. ``parameters`` is recorded verbatim in the manifest."""

    variant_id: str
    algorithm: str
    version: str
    description: str
    parameters: Mapping[str, Any]
    derived: bool = True

    def record(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "algorithm": self.algorithm,
            "version": self.version,
            "description": self.description,
            "parameters": dict(self.parameters),
            "derived": self.derived,
        }


ILLUMINATION = VariantSpec(
    variant_id="illumination-normalized",
    algorithm="flat_field_gaussian_background",
    version="penforge-volunteer-illumination-v1",
    description=(
        "Divide by a large-radius Gaussian background estimate and rescale to a fixed "
        "white point; ink geometry is preserved because the correction is multiplicative."
    ),
    parameters={
        "background_radius_px": 48.0,
        "white_point": 250.0,
        "background_floor": 0.15,
        "resampling": "none",
        "channels": "same gain for r, g and b",
    },
)

CONTRAST = VariantSpec(
    variant_id="contrast-normalized",
    algorithm="percentile_luminance_stretch",
    version="penforge-volunteer-contrast-v1",
    description=(
        "One linear gain/offset from fixed luminance percentiles, applied identically to "
        "all channels, with a fixed no-op guard when the observed range is too small."
    ),
    parameters={
        "paper_percentile": 50.0,
        "ink_percentile": 1.0,
        "minimum_observed_range": 12.0,
        "target_white": 250.0,
        "target_contrast": 220.0,
        "max_gain": 2.2,
        "channels": "same gain for r, g and b",
    },
)

RULED = VariantSpec(
    variant_id="ruled-line-aware",
    algorithm="edge_to_edge_row_suppression",
    version="penforge-volunteer-ruled-lines-v1",
    description=(
        "Remove only wide dark row runs that do not continue vertically, then interpolate "
        "them from their neighbours; refuse if too much ink would go."
    ),
    parameters={
        "ink_threshold": float(INK_THRESHOLD),
        "row_dark_fraction": 0.5,
        "continuation_gap_rows": 3,
        "continuation_probe_rows": 4,
        "continuation_ink_threshold": 85.0,
        "max_vertical_continuation": 0.35,
        "max_glyph_ink_loss": 0.12,
        "max_removed_row_fraction": 0.25,
        "fill": "linear_interpolation_between_neighbour_rows",
    },
)

COMBINED = VariantSpec(
    variant_id="combined",
    algorithm="illumination_then_rules_then_paper_padding",
    version="penforge-volunteer-combined-v1",
    description=(
        "Illumination flattening, conservative rule removal and square padding with the "
        "estimated paper colour; glyph size and aspect ratio are never changed."
    ),
    parameters={
        "illumination": dict(ILLUMINATION.parameters),
        "ruled_lines": dict(RULED.parameters),
        "padding": "centred_paper_colour_to_384",
        "paper_colour": "median of the outer 5 percent border",
    },
)

RAW = VariantSpec(
    variant_id="raw",
    algorithm="identity",
    version="penforge-volunteer-raw-v1",
    description=(
        "Byte-identical copy of the baseline pilot crop; the already measured control, "
        "never regenerated from page pixels."
    ),
    parameters={"operation": "byte_identical_copy"},
    derived=False,
)

VARIANTS: tuple[VariantSpec, ...] = (RAW, ILLUMINATION, CONTRAST, RULED, COMBINED)
VARIANT_IDS: tuple[str, ...] = tuple(spec.variant_id for spec in VARIANTS)

#: Fixed selection rule, declared before any inference and repeated in the report.
SELECTION_RULE = (
    "highest_exact_accuracy_then_lowest_non_case_error_count_"
    "then_highest_casefolded_accuracy_then_variant_id"
)


@dataclass
class VariantResult:
    image: Image.Image
    flags: dict[str, Any] = field(default_factory=dict)


def _percentile(ordered: Sequence[int], quantile: float) -> float:
    if not ordered:
        raise ValueError("volunteer_variant_percentile_requires_values")
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _split(image: Image.Image) -> tuple[list[int], list[int], list[int]]:
    red, green, blue = image.split()
    return list(red.getdata()), list(green.getdata()), list(blue.getdata())


def illumination_normalized(image: Image.Image, spec: VariantSpec = ILLUMINATION) -> VariantResult:
    """Flatten uneven lighting with one multiplicative, geometry-preserving correction."""
    parameters = spec.parameters
    radius = float(parameters["background_radius_px"])
    white_point = float(parameters["white_point"])
    floor = float(parameters["background_floor"])
    grey = image.convert("L")
    background = grey.filter(ImageFilter.GaussianBlur(radius=radius))
    background_pixels = list(background.getdata())
    gains = [white_point / max(value, floor * white_point) for value in background_pixels]
    channels = [
        [
            max(0, min(255, int(round(value * gain))))
            for value, gain in zip(data, gains, strict=True)
        ]
        for data in _split(image)
    ]
    result = Image.merge(
        "RGB",
        [_channel_image(image.size, channel) for channel in channels],
    )
    return VariantResult(
        image=result,
        flags={
            "mean_gain": round(sum(gains) / len(gains), 6),
            "min_gain": round(min(gains), 6),
            "max_gain": round(max(gains), 6),
        },
    )


def _channel_image(size: tuple[int, int], values: Sequence[int]) -> Image.Image:
    channel = Image.new("L", size)
    channel.putdata(list(values))
    return channel


def contrast_normalized(image: Image.Image, spec: VariantSpec = CONTRAST) -> VariantResult:
    """One global linear stretch anchored on the paper level; guarded and gain-capped.

    The paper level is the median luminance because paper dominates an isolated-letter
    crop; the ink level is the first percentile. Anchoring the offset on the paper level
    keeps the page background near white whatever the observed range is, so a sparse
    crop cannot be turned into a dark rectangle by a low second percentile.
    """
    parameters = spec.parameters
    paper_quantile = float(parameters["paper_percentile"]) / 100
    ink_quantile = float(parameters["ink_percentile"]) / 100
    minimum_range = float(parameters["minimum_observed_range"])
    target_white = float(parameters["target_white"])
    target_contrast = float(parameters["target_contrast"])
    max_gain = float(parameters["max_gain"])
    ordered = sorted(image.convert("L").getdata())
    paper = _percentile(ordered, paper_quantile)
    ink = _percentile(ordered, ink_quantile)
    observed = paper - ink
    if observed < minimum_range:
        return VariantResult(
            image=image.copy(),
            flags={"stretched": False, "observed_range": round(observed, 3), "gain": 1.0},
        )
    gain = min(max_gain, target_contrast / observed)
    offset = target_white - paper * gain
    channels = [
        [max(0, min(255, int(round(value * gain + offset)))) for value in data]
        for data in _split(image)
    ]
    return VariantResult(
        image=Image.merge("RGB", [_channel_image(image.size, channel) for channel in channels]),
        flags={
            "stretched": True,
            "observed_range": round(observed, 3),
            "gain": round(gain, 6),
            "offset": round(offset, 6),
            "gain_capped": target_contrast / observed > max_gain,
        },
    )


def _rule_runs(grey: Image.Image, *, parameters: Mapping[str, Any]) -> list[list[int]]:
    """Contiguous rows that look like a notebook rule rather than part of the letter.

    A ruled line is a wide dark row that does not continue vertically: just above and
    just below the run, the same columns are paper. A letter's own horizontal stroke is
    always supported by the rest of the letter (a T bar has its stem, an L foot its
    upright, the bottom of an E its three arms), so it survives this test.
    """
    width, height = grey.size
    threshold = float(parameters["ink_threshold"])
    pixels = list(grey.getdata())
    candidates: list[int] = []
    for row in range(height):
        strip = pixels[row * width : (row + 1) * width]
        dark = sum(1 for value in strip if value < threshold)
        if dark / width >= float(parameters["row_dark_fraction"]):
            candidates.append(row)
    if not candidates:
        return []
    runs: list[list[int]] = []
    current: list[int] = [candidates[0]]
    for row in candidates[1:]:
        if row == current[-1] + 1:
            current.append(row)
        else:
            runs.append(current)
            current = [row]
    runs.append(current)
    gap = int(parameters["continuation_gap_rows"])
    probe = int(parameters["continuation_probe_rows"])
    limit = float(parameters["max_vertical_continuation"])
    strict = float(parameters["continuation_ink_threshold"])
    accepted: list[list[int]] = []
    for run in runs:
        columns: set[int] = set()
        for row in run:
            strip = pixels[row * width : (row + 1) * width]
            columns.update(index for index, value in enumerate(strip) if value < threshold)
        if not columns:
            continue
        # Skip the anti-aliased halo around the run; a rule fades out there, a letter
        # stroke keeps its dark core a few rows further away.
        probe_rows = list(range(max(0, run[0] - gap - probe), max(0, run[0] - gap))) + list(
            range(min(height, run[-1] + gap + 1), min(height, run[-1] + gap + 1 + probe))
        )
        if not probe_rows:
            accepted.append(run)
            continue
        supported = 0
        for column in columns:
            if any(pixels[row * width + column] < strict for row in probe_rows):
                supported += 1
        if supported / len(columns) <= limit:
            accepted.append(run)
    return accepted


def ruled_line_suppression(image: Image.Image, spec: VariantSpec = RULED) -> VariantResult:
    """Remove edge-to-edge horizontal rules, or refuse and return the crop unchanged."""
    parameters = spec.parameters
    grey = image.convert("L")
    width, height = grey.size
    grey_pixels = list(grey.getdata())
    rule_runs = _rule_runs(grey, parameters=parameters)
    rule_rows = [row for run in rule_runs for row in run]
    if not rule_rows:
        return VariantResult(image=image.copy(), flags={"refused": False, "removed_rows": 0})
    is_rule = set(rule_rows)
    strict = float(parameters["continuation_ink_threshold"])
    core = [
        [
            index
            for index, value in enumerate(grey_pixels[row * width : (row + 1) * width])
            if value < strict
        ]
        for row in range(height)
    ]
    glyph_ink = sum(len(core[row]) for row in range(height) if row not in is_rule)
    if glyph_ink == 0:
        return VariantResult(image=image.copy(), flags={"refused": False, "removed_rows": 0})
    # A removal damages the letter only where the rule runs through the letter's own
    # dark core: a rule above, below or beside the glyph costs nothing even when it
    # carries a large share of the crop's dark pixels. The probe skips the anti-aliased
    # halo, which is much lighter than the pen ink.
    reach = int(parameters["continuation_gap_rows"])
    loss = 0
    for row in rule_rows:
        probes = list(range(max(0, row - reach), row)) + list(
            range(row + 1, min(height, row + 1 + reach))
        )
        for column in core[row]:
            if any(column in core[probe] for probe in probes if probe not in is_rule):
                loss += 1
    loss_fraction = loss / glyph_ink
    if loss_fraction > float(parameters["max_glyph_ink_loss"]):
        return VariantResult(
            image=image.copy(),
            flags={
                "refused": True,
                "reason": "rule_rows_cross_the_letter",
                "removed_rows": len(rule_rows),
                "glyph_ink_loss": round(loss_fraction, 6),
            },
        )
    if len(rule_rows) / height > float(parameters["max_removed_row_fraction"]):
        return VariantResult(
            image=image.copy(),
            flags={
                "refused": True,
                "reason": "too_many_rule_rows",
                "removed_rows": len(rule_rows),
                "glyph_ink_loss": round(loss_fraction, 6),
            },
        )
    rows = [grey_pixels[row * width : (row + 1) * width] for row in range(height)]
    replacement: list[list[int]] = [list(values) for values in rows]
    for row in rule_rows:
        above = next(
            (candidate for candidate in range(row - 1, -1, -1) if candidate not in is_rule), None
        )
        below = next(
            (candidate for candidate in range(row + 1, height) if candidate not in is_rule), None
        )
        if above is None and below is None:
            continue
        for column in range(width):
            if above is None:
                if below is None:
                    continue
                value = rows[below][column]
            elif below is None:
                value = rows[above][column]
            else:
                step = (row - above) / (below - above)
                value = round(rows[above][column] * (1 - step) + rows[below][column] * step)
            replacement[row][column] = max(0, min(255, int(value)))
    result = _rebuild_rgb(image, replacement)
    return VariantResult(
        image=result,
        flags={
            "refused": False,
            "removed_rows": len(rule_rows),
            "glyph_ink_loss": round(loss_fraction, 6),
        },
    )


def _rebuild_rgb(image: Image.Image, grey_rows: Sequence[Sequence[int]]) -> Image.Image:
    """Keep colour where it exists: reuse the cleaned luminance as a multiplicative mask."""
    grey_after = _channel_image(image.size, [value for row in grey_rows for value in row])
    grey_before = image.convert("L")
    before = list(grey_before.getdata())
    after = list(grey_after.getdata())
    channels = []
    for data in _split(image):
        cleaned = []
        for original, low, high in zip(data, before, after, strict=True):
            if low <= 1:
                cleaned.append(original)
                continue
            cleaned.append(max(0, min(255, int(round(original * high / low)))))
        channels.append(_channel_image(image.size, cleaned))
    return Image.merge("RGB", channels)


def _paper_colour(image: Image.Image) -> tuple[int, int, int]:
    """Median colour of the outer 5 percent border: the crop's own paper tone."""
    width, height = image.size
    border = max(1, round(0.05 * min(width, height)))
    pixels = image.load()
    if pixels is None:
        raise ValueError("volunteer_variant_paper_sample_requires_pixels")
    samples: list[tuple[int, int, int]] = []
    for x in range(width):
        for y in range(border):
            samples.append(_pixel(pixels[x, y]))
            samples.append(_pixel(pixels[x, height - 1 - y]))
    for y in range(height):
        for x in range(border):
            samples.append(_pixel(pixels[x, y]))
            samples.append(_pixel(pixels[width - 1 - x, y]))
    channels = [[sample[index] for sample in samples] for index in range(3)]
    return (
        sorted(channels[0])[len(channels[0]) // 2],
        sorted(channels[1])[len(channels[1]) // 2],
        sorted(channels[2])[len(channels[2]) // 2],
    )


def _pixel(value: object) -> tuple[int, int, int]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise ValueError("volunteer_variant_requires_rgb_pixels")
    return (int(value[0]), int(value[1]), int(value[2]))


def paper_pad(image: Image.Image) -> VariantResult:
    """Centre the crop on a fixed square canvas filled with its own paper colour."""
    if image.width > CANVAS or image.height > CANVAS:
        scale = min(CANVAS / image.width, CANVAS / image.height)
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    side = max(image.width, image.height)
    paper = _paper_colour(image)
    canvas = Image.new("RGB", (side, side), paper)
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    padding = side - min(image.width, image.height)
    if side != CANVAS:
        canvas = canvas.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)
    return VariantResult(image=canvas, flags={"padding_pixels": padding, "paper": list(paper)})


def apply_variant(image: Image.Image, spec: VariantSpec) -> VariantResult:
    """Apply exactly one fixed condition; unknown ids are refused."""
    if spec.variant_id == RAW.variant_id:
        return VariantResult(image=image.copy(), flags={"copied": True})
    if spec.variant_id == ILLUMINATION.variant_id:
        return illumination_normalized(image, spec)
    if spec.variant_id == CONTRAST.variant_id:
        return contrast_normalized(image, spec)
    if spec.variant_id == RULED.variant_id:
        return ruled_line_suppression(image, spec)
    if spec.variant_id == COMBINED.variant_id:
        first = illumination_normalized(image, ILLUMINATION)
        second = ruled_line_suppression(first.image, RULED)
        third = paper_pad(second.image)
        return VariantResult(
            image=third.image,
            flags={
                "illumination": first.flags,
                "ruled_lines": second.flags,
                "padding": third.flags,
            },
        )
    raise ValueError(f"volunteer_unknown_variant:{spec.variant_id}")


def variant_by_id(variant_id: str) -> VariantSpec:
    for spec in VARIANTS:
        if spec.variant_id == variant_id:
            return spec
    raise ValueError(f"volunteer_unknown_variant:{variant_id}")


def _load_crop(root: Path, sample: ManifestSample) -> Image.Image:
    path = root / sample.crop_path
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"volunteer_variant_crop_missing:{sample.sample_id}")
    if path.stat().st_size > MAX_CROP_BYTES:
        raise ValueError(f"volunteer_variant_crop_too_large:{sample.sample_id}")
    if digest_file(path) != sample.crop_sha256:
        raise ValueError(f"volunteer_variant_crop_changed:{sample.sample_id}")
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError(f"volunteer_variant_crop_format_changed:{sample.sample_id}")
        image.load()
        return image.convert("RGB")


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - older Pillow fallback
        return ImageFont.load_default()


def variant_sheet(
    samples: Sequence[ManifestSample],
    images: Mapping[str, Image.Image],
    *,
    variant_id: str,
    writer_id: str,
    columns: int = 13,
    cell: int = 128,
) -> bytes:
    """Inspection sheet for one condition and one writer, mirroring the pilot sheets."""
    rows = (len(samples) + columns - 1) // columns
    header, footer = 46, 30
    width = columns * cell
    height = header + rows * (cell + 26) + footer
    sheet = Image.new("RGB", (width, height), (247, 244, 238))
    draw = ImageDraw.Draw(sheet)
    title_font, label_font, small_font = _font(17), _font(14), _font(12)
    draw.text(
        (10, 9),
        f"{variant_id} - {writer_id} - {len(samples)} crops - PRIVATE, do not distribute",
        fill=(40, 30, 25),
        font=title_font,
    )
    draw.text(
        (10, 28),
        "Deterministic derivative of the frozen baseline crop. Labels are unchanged.",
        fill=(90, 78, 70),
        font=small_font,
    )
    for index, sample in enumerate(samples):
        column, row = index % columns, index // columns
        x = column * cell
        y = header + row * (cell + 26)
        thumb = images[sample.sample_id].resize((cell - 12, cell - 12), Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x + 6, y + 2))
        draw.rectangle((x + 5, y + 1, x + cell - 6, y + cell - 8), outline=(198, 186, 176))
        draw.text((x + 6, y + cell - 6), sample.sample_id, fill=(35, 32, 30), font=label_font)
        draw.text(
            (x + 6, y + cell + 8), f"truth={sample.truth}", fill=(120, 60, 40), font=small_font
        )
    draw.text(
        (10, height - footer + 6),
        "Private five-writer pilot. Isolated letter crops only; contact sheet for inspection.",
        fill=(90, 78, 70),
        font=small_font,
    )
    return _png_bytes(sheet)


def _measurement(image: Image.Image) -> dict[str, Any]:
    from research.english.volunteer.audit import measure, measurement_dict

    return measurement_dict(measure(image))


def _load_pilot(run: Path) -> tuple[Manifest, Path]:
    root = private_run_directory(run)
    manifest_path = root / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    if len(manifest.samples) != EXPECTED_SAMPLES:
        raise ValueError("volunteer_variant_source_inventory_changed")
    return manifest, root


def prepare(pilot_run: Path, run: Path) -> dict[str, Any]:
    """Generate every fixed condition into a new private run directory.

    Refuses an evaluated pilot, a modified crop, an altered manifest and any existing
    output whose bytes would change. Deterministic: an identical re-run writes nothing.
    """
    manifest, pilot_root = _load_pilot(pilot_run)
    root = private_run_directory(run)
    if root == pilot_root:
        raise ValueError("volunteer_variants_need_their_own_run_directory")
    for name in (REPORT_NAME, PREDICTIONS_NAME):
        if not (pilot_root / name).is_file():
            raise ValueError(f"volunteer_variant_source_pilot_incomplete:{name}")
    outputs: dict[str, bytes] = {}
    measurements: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    for spec in VARIANTS:
        variant_dir = f"{VARIANTS_DIR}/{spec.variant_id}"
        images: dict[str, Image.Image] = {}
        for sample in manifest.samples:
            source = _load_crop(pilot_root, sample)
            result = apply_variant(source, spec)
            payload = _png_bytes(result.image)
            digest = hashlib.sha256(payload).hexdigest()
            if spec.variant_id == RAW.variant_id and digest != sample.crop_sha256:
                raise ValueError(f"volunteer_raw_variant_is_not_byte_identical:{sample.sample_id}")
            outputs[f"{variant_dir}/images/{digest}.png"] = payload
            images[sample.sample_id] = result.image
            entries.append(
                {
                    "variant_id": spec.variant_id,
                    "sample_id": sample.sample_id,
                    "writer_id": sample.writer_id,
                    "group": sample.group,
                    "truth": sample.truth,
                    "source_crop_sha256": sample.crop_sha256,
                    "crop_sha256": digest,
                    "crop_path": f"{variant_dir}/images/{digest}.png",
                    "algorithm": spec.algorithm,
                    "algorithm_version": spec.version,
                    "parameters": dict(spec.parameters),
                    "flags": result.flags,
                    "unchanged_bytes": digest == sample.crop_sha256,
                }
            )
        measurements[spec.variant_id] = _summarise(spec, entries)
        for writer_id in WRITERS:
            writer_samples = [
                sample for sample in manifest.samples if sample.writer_id == writer_id
            ]
            outputs[f"{variant_dir}/sheet-{writer_id}.png"] = variant_sheet(
                writer_samples, images, variant_id=spec.variant_id, writer_id=writer_id
            )
    variant_manifest = {
        "version": VARIANTS_VERSION,
        "pilot": "private_five_writer_exploratory_pilot",
        "scope": "isolated_uppercase_and_lowercase_letter_crops_only",
        "source_crop_manifest_sha256": manifest.sha256,
        "source_crop_generation_version": manifest.crop_generation["version"],
        "variant_ids": list(VARIANT_IDS),
        "variants": [spec.record() for spec in VARIANTS],
        "selection_rule": SELECTION_RULE,
        "entries": entries,
        "measurements": measurements,
        "included_samples": len(manifest.samples),
        "model_execution": False,
        "training_performed": False,
    }
    manifest_bytes = (
        json.dumps(variant_manifest, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()
    outputs[MANIFEST_NAME_OUT] = manifest_bytes
    outputs[PILOT_LINK_NAME] = (
        json.dumps(
            {
                "pilot_run": str(pilot_root),
                "crop_manifest_sha256": manifest.sha256,
                "predictions_sha256": digest_file(pilot_root / PREDICTIONS_NAME),
                "report_sha256": digest_file(pilot_root / REPORT_NAME),
                "baseline_accuracy": 0.5076923076923077,
                "original_crops_modified": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    outputs[PREPARED_NAME] = (
        json.dumps(
            {
                "status": "prepared_not_evaluated",
                "pilot_run": str(pilot_root),
                "variant_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "variant_ids": list(VARIANT_IDS),
                "included_samples": len(manifest.samples),
                "expected_samples": EXPECTED_SAMPLES,
                "selection_rule": SELECTION_RULE,
                "model_execution": False,
                "training_performed": False,
                "next_stage": "owner_compare",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    written = 0
    for relative, payload in sorted(outputs.items()):
        target = root / relative
        if target.is_symlink():
            raise ValueError(f"volunteer_variant_output_must_not_be_symlink:{relative}")
        if target.exists():
            if target.read_bytes() != payload:
                raise FileExistsError(f"volunteer_variant_existing_output_differs:{relative}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.with_name(target.name + ".partial")
        with temporary.open("wb") as stream:
            stream.write(payload)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        written += 1
    return {
        "status": "prepared_not_evaluated" if written else "verified_existing_variants",
        "run": str(root),
        "variant_ids": list(VARIANT_IDS),
        "included_samples": len(manifest.samples),
        "files_written": written,
        "model_execution": False,
        "training_performed": False,
        "measurements": measurements,
    }


def _summarise(spec: VariantSpec, entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    mine = [entry for entry in entries if entry["variant_id"] == spec.variant_id]
    if not mine:
        raise ValueError(f"volunteer_variant_measurements_empty:{spec.variant_id}")
    changed = sum(1 for entry in mine if not entry["unchanged_bytes"])
    return {
        "samples": len(mine),
        "crops_with_changed_bytes": changed,
        "crops_byte_identical_to_baseline": len(mine) - changed,
        "refused_crops": sum(1 for entry in mine if entry["flags"].get("refused")),
        "algorithm": spec.algorithm,
        "version": spec.version,
    }


def variant_measurements(run: Path, variant_id: str) -> dict[str, Any]:
    """Mean image-quality measurements for one condition, over its 260 crops."""
    root = private_run_directory(run)
    payload = json.loads((root / MANIFEST_NAME_OUT).read_text(encoding="utf-8"))
    entries = [entry for entry in payload["entries"] if entry["variant_id"] == variant_id]
    if not entries:
        raise ValueError(f"volunteer_unknown_variant:{variant_id}")
    values = [_measurement(_load_variant_crop(root, entry)) for entry in entries]
    total = len(values)
    return {
        "variant_id": variant_id,
        "samples": total,
        "mean_brightness": round(sum(value["brightness"] for value in values) / total, 3),
        "mean_contrast": round(sum(value["contrast"] for value in values) / total, 3),
        "mean_ink_fraction": round(sum(value["ink_fraction"] for value in values) / total, 6),
        "mean_rule_strength": round(sum(value["rule_strength"] for value in values) / total, 4),
        "crops_with_a_ruled_row": sum(1 for value in values if value["rule_rows"] > 0),
        "crops_touching_an_edge": sum(1 for value in values if value["touches_edge"]),
    }


def _load_variant_crop(root: Path, entry: Mapping[str, Any]) -> Image.Image:
    path = root / str(entry["crop_path"])
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"volunteer_variant_derivative_missing:{entry['sample_id']}")
    if digest_file(path) != entry["crop_sha256"]:
        raise ValueError(f"volunteer_variant_derivative_changed:{entry['sample_id']}")
    with Image.open(path) as image:
        image.load()
        return image.convert("RGB")


def load_variant_manifest(run: Path) -> dict[str, Any]:
    """Strictly validate a prepared variant manifest and its derivatives before use."""
    root = private_run_directory(run)
    path = root / MANIFEST_NAME_OUT
    if path.is_symlink() or not path.is_file():
        raise ValueError("volunteer_variant_manifest_missing_or_unsafe")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != VARIANTS_VERSION:
        raise ValueError("volunteer_variant_manifest_version_changed")
    if list(payload.get("variant_ids", [])) != list(VARIANT_IDS):
        raise ValueError("volunteer_variant_inventory_changed")
    recorded = {
        item["variant_id"]: item for item in payload.get("variants", []) if "variant_id" in item
    }
    if set(recorded) != set(VARIANT_IDS):
        raise ValueError("volunteer_variant_record_inventory_changed")
    for spec in VARIANTS:
        item = recorded[spec.variant_id]
        if (
            item.get("algorithm") != spec.algorithm
            or item.get("version") != spec.version
            or item.get("parameters") != dict(spec.parameters)
        ):
            raise ValueError(f"volunteer_variant_record_changed:{spec.variant_id}")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != EXPECTED_SAMPLES * len(VARIANTS):
        raise ValueError("volunteer_variant_entry_count_changed")
    by_variant: dict[str, list[Mapping[str, Any]]] = {variant_id: [] for variant_id in VARIANT_IDS}
    for entry in entries:
        variant_id = entry.get("variant_id")
        if variant_id not in by_variant:
            raise ValueError("volunteer_variant_entry_names_an_unknown_condition")
        by_variant[variant_id].append(entry)
    for variant_id, values in by_variant.items():
        if len(values) != EXPECTED_SAMPLES:
            raise ValueError(f"volunteer_variant_sample_count_changed:{variant_id}")
        if len({entry["sample_id"] for entry in values}) != len(values):
            raise ValueError(f"volunteer_variant_duplicate_sample:{variant_id}")
    payload["by_variant"] = by_variant
    payload["manifest_sha256"] = digest_file(path)
    return payload


def main() -> None:  # pragma: no cover - exercised through the owner wrapper
    import sys

    if len(sys.argv) != 3:
        print(
            "usage: python -m research.english.volunteer.variants <pilot-run> <variant-run>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    value = prepare(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps(value, sort_keys=True, allow_nan=False))


__all__ = [
    "COMBINED",
    "CONTRAST",
    "ILLUMINATION",
    "MANIFEST_NAME_OUT",
    "PILOT_LINK_NAME",
    "PREPARED_NAME",
    "RAW",
    "RULED",
    "SELECTION_RULE",
    "VARIANTS",
    "VARIANTS_VERSION",
    "VARIANT_IDS",
    "VariantResult",
    "VariantSpec",
    "apply_variant",
    "contrast_normalized",
    "illumination_normalized",
    "load_variant_manifest",
    "paper_pad",
    "prepare",
    "ruled_line_suppression",
    "variant_by_id",
    "variant_measurements",
]
