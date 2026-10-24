"""Private file boundaries and deterministic image identity."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageOps

from research.english.nist import sha
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file


def external(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("r5_private_root_must_not_be_symlink")
    path = path.expanduser().resolve()
    if path == ROOT or path.is_relative_to(ROOT):
        raise ValueError("r5_raw_staging_must_remain_outside_repository")
    return path


def checked(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("r5_missing_or_unsafe_private_file")
    return path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def jsonl(rows) -> bytes:
    return b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        for row in sorted(rows, key=lambda row: row["id"])
    )


def write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")
    path.chmod(0o600)


def image_identity(image: Image.Image) -> dict:
    """Raw grayscale and declared normalized identity; normalized bytes are never model inputs."""
    if image.format != "PNG" or image.size != (128, 128) or getattr(image, "n_frames", 1) != 1:
        raise ValueError("r5_source_requires_single_publisher_128_square_png")
    gray = image.convert("L")
    ink = ImageOps.invert(gray).point(lambda pixel: 255 if pixel >= 128 else 0)
    bbox = ink.getbbox()
    if bbox is None or gray.getextrema()[0] == gray.getextrema()[1]:
        raise ValueError("r5_blank_crop")
    cropped = ink.crop(bbox)
    scale = min(56 / cropped.width, 56 / cropped.height)
    resized = cropped.resize(
        (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale))),
        Image.Resampling.NEAREST,
    )
    canvas = Image.new("L", (64, 64), 0)
    canvas.paste(resized, ((64 - resized.width) // 2, (64 - resized.height) // 2))
    return {
        "pixel_hash": sha(gray.tobytes()),
        "normalized_pixel_hash": sha(b"r5-binary-ink-bbox-56-in-64-v1\0" + canvas.tobytes()),
        "bbox": list(bbox),
        "ink_width_px": bbox[2] - bbox[0],
        "ink_height_px": bbox[3] - bbox[1],
        "ink_pixels": ink.histogram()[255],
        "canvas_width_px": 128,
        "canvas_height_px": 128,
        "observed_baseline": None,
        "page_scale": None,
    }


def file_pins(root: Path, names) -> dict:
    return {name: digest_file(checked(root, name)) for name in names}


def code_inventory() -> dict:
    paths = [
        ROOT / "research/english/r5_contract.py",
        ROOT / "research/configs/english-r5-data.json",
    ]
    paths += sorted((ROOT / "research/english/r5_data").glob("*.py"))
    paths += [
        ROOT / name
        for name in (
            "research/english/nist.py",
            "research/english/acquire.py",
            "research/english/r4_study.py",
            "research/english/study.py",
            "pyproject.toml",
            "uv.lock",
        )
    ]
    return {str(path.relative_to(ROOT)): digest_file(path) for path in paths}


def load_config(path: Path | None = None) -> dict:
    value = json.loads((path or ROOT / "research/configs/english-r5-data.json").read_text())
    from research.english.r5_contract import LETTERS

    if value.get("version") != "english-r5-data-v1" or value.get("class_order") != LETTERS:
        raise ValueError("r5_data_config_version_or_class_order")
    caps = value.get("per_class_caps", {})
    if set(caps) != {"train", "validation", "test", "school_test"} or any(
        type(n) is not int or not 1 <= n <= 512 for n in caps.values()
    ):
        raise ValueError("r5_invalid_partition_caps")
    if type(value.get("seed")) is not int or not 0 <= value["seed"] < 2**32:
        raise ValueError("r5_invalid_seed")
    if type(value.get("minimum_evaluation_per_class")) is not int or not 16 <= value[
        "minimum_evaluation_per_class"
    ] <= min(caps[s] for s in ("validation", "test", "school_test")):
        raise ValueError("r5_invalid_evaluation_minimum")
    return value


__all__ = [
    "canonical_hash",
    "digest_file",
    "external",
    "checked",
    "read_jsonl",
    "jsonl",
    "write_json",
    "image_identity",
    "file_pins",
    "code_inventory",
    "load_config",
]
