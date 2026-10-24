"""Read and stage NIST letters, pending curator admission. No ML execution."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import string
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image
from PIL import __version__ as pillow_version

from research.english.acquire import SOURCES, inspect
from research.preparation.archive_audit import ROOT, external, safe_name

LETTERS = string.ascii_uppercase + string.ascii_lowercase
WRITER = re.compile(
    r"by_write/hsf_([01234678])/f(\d{4})_(\d{2})/([cdlu])\2_\3/\4\2_\3_(\d{5})\.png"
)
CLASSES = re.compile(
    r"by_class/([0-9a-fA-F]{2})/(?:hsf_[01234678]|train_[0-9a-fA-F]{2})/[^/]+\.png"
)
RANGES = {
    0: (0, 499),
    1: (500, 999),
    2: (1000, 1499),
    3: (1500, 2099),
    4: (2100, 2599),
    6: (3100, 3599),
    7: (3600, 4099),
    8: (4100, 4169),
}


class LetterCoverageError(ValueError):
    def __init__(self, report: dict):
        super().__init__("nist_insufficient_reviewable_letters_no_silent_class_omission")
        self.report = report


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def writer_parts(name: str) -> tuple[int, str, str, str] | None:
    match = WRITER.fullmatch(name)
    if not match:
        return None
    partition, writer, form, field, _ = match.groups()
    lower, upper = RANGES[int(partition)]
    if not lower <= int(writer) <= upper:
        raise ValueError("nist_writer_outside_documented_partition")
    return int(partition), writer, form, field


def members(archive: zipfile.ZipFile):
    entries = archive.infolist()
    if len(entries) > 2_000_000:
        raise ValueError("nist_archive_entry_ceiling")
    names, total = set(), 0
    for entry in entries:
        name = safe_name(entry.filename)
        if name != entry.filename.rstrip("/") or name in names:
            raise ValueError("nist_noncanonical_or_duplicate_member")
        names.add(name)
        mode = stat.S_IFMT(entry.external_attr >> 16)
        allowed = {0, stat.S_IFDIR} if entry.is_dir() else {0, stat.S_IFREG}
        if mode not in allowed or entry.flag_bits & 1:
            raise ValueError("nist_links_special_or_encrypted_member")
        if entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("nist_unsupported_compression")
        if entry.file_size > 1_000_000:
            raise ValueError("nist_member_size_ceiling")
        total += entry.file_size
        if total > 8_000_000_000:
            raise ValueError("nist_archive_expansion_ceiling")
        yield entry


def writer_splits(writers: dict[str, int], seed: int) -> dict[str, str]:
    def rank(writer):
        return canonical([seed, "writer", writer])

    adults = sorted((w for w, p in writers.items() if p not in {4, 8}), key=rank)
    validation_n = max(1, len(adults) // 10)
    validation, training = adults[:validation_n], adults[validation_n:]
    test = sorted((w for w, p in writers.items() if p == 4), key=rank)
    result = {w: "train" for w in training[:128]}
    result.update((w, "validation") for w in validation[:16])
    result.update((w, "test") for w in test[:16])
    if set(result.values()) != {"train", "validation", "test"}:
        raise ValueError("nist_requires_three_nonempty_writer_partitions")
    return result


def select(rows: list[dict], caps: dict[str, int], seed: int) -> tuple[list[dict], dict]:
    # Pixel identity includes different PNG encodings. Quarantine every ambiguous
    # group, even when its current samples happen to share a split.
    groups = defaultdict(list)
    for row in rows:
        groups[row["pixel_hash"]].append(row)
    conflicts = {
        r["id"]
        for group in groups.values()
        if len({r["writer"] for r in group}) > 1 or len({r["label"] for r in group}) > 1
        for r in group
    }
    selected = []
    for split, maximum in caps.items():
        for label in LETTERS:
            pool = sorted(
                (
                    r
                    for r in rows
                    if r["split"] == split and r["label"] == label and r["id"] not in conflicts
                ),
                key=lambda r: canonical([seed, r["id"]]),
            )
            chosen, writers, pixels = [], set(), set()
            for row in pool:
                if row["writer"] in writers or row["pixel_hash"] in pixels:
                    continue
                writers.add(row["writer"])
                pixels.add(row["pixel_hash"])
                chosen.append(row)
                if len(chosen) == maximum:
                    break
            if len(chosen) != maximum:
                raise LetterCoverageError(
                    {
                        "status": "insufficient_letter_coverage",
                        "split": split,
                        "letter": label,
                        "required_distinct_writers": maximum,
                        "available_distinct_writers": len(chosen),
                        "candidate_images": len(rows),
                        "pixel_ambiguities_quarantined": len(conflicts),
                        "coverage": {
                            part: {
                                char: len(
                                    {
                                        row["writer"]
                                        for row in rows
                                        if row["split"] == part
                                        and row["label"] == char
                                        and row["id"] not in conflicts
                                    }
                                )
                                for char in LETTERS
                            }
                            for part in caps
                        },
                    }
                )
            selected.extend(chosen)
    return sorted(selected, key=lambda r: r["id"]), {
        "candidate_images": len(rows),
        "pixel_ambiguities_quarantined": len(conflicts),
    }


def audit(sources: Path, destination: Path, *, seed: int = 20261021, caps=None) -> dict:
    sources, destination = external(sources), external(destination)
    caps = {"train": 40, "validation": 2, "test": 5} if caps is None else caps
    if set(caps) != {"train", "validation", "test"} or any(
        type(n) is not int or not 1 <= n <= 64 for n in caps.values()
    ):
        raise ValueError("nist_invalid_partition_caps")
    if (
        destination.exists()
        or destination.is_relative_to(sources)
        or sources.is_relative_to(destination)
    ):
        raise ValueError("nist_new_staging_must_not_overlap_sources")
    receipts = {
        name: inspect(sources / name, *SOURCES[name]) for name in ("by_write.zip", "by_class.zip")
    }
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".nist-preparation-", dir=destination.parent))
    db = sqlite3.connect(temporary / "class-index.sqlite")
    try:
        db.execute("CREATE TABLE classes (hash TEXT PRIMARY KEY, label TEXT, ambiguous INTEGER)")
        class_images, writer_images, unmapped, blank = 0, 0, 0, 0
        with zipfile.ZipFile(sources / "by_class.zip") as archive:
            for entry in members(archive):
                if entry.is_dir():
                    continue
                data = archive.read(entry)  # Exhausting each member checks its ZIP CRC.
                match = CLASSES.fullmatch(entry.filename)
                if not match:
                    if Path(entry.filename).suffix.lower() not in {".txt", ".cls", ".mit"}:
                        raise ValueError("nist_class_archive_structure_changed")
                    continue
                label = chr(int(match[1], 16))
                if label not in LETTERS + string.digits:
                    raise ValueError("nist_unexpected_character_class")
                checksum = sha(data)
                db.execute(
                    "INSERT INTO classes VALUES (?,?,0) ON CONFLICT(hash) DO UPDATE SET ambiguous=CASE WHEN label != excluded.label THEN 1 ELSE ambiguous END",
                    (checksum, label),
                )
                class_images += 1
        db.commit()
        writers: dict[str, int] = {}
        with zipfile.ZipFile(sources / "by_write.zip") as archive:
            for entry in members(archive):
                parsed = writer_parts(entry.filename)
                if parsed:
                    partition, writer, _, _ = parsed
                    if writer in writers and writers[writer] != partition:
                        raise ValueError("nist_writer_occurs_in_multiple_partitions")
                    writers[writer] = partition
            splits = writer_splits(writers, seed)
            rows = []
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                data = archive.read(entry)
                parsed = writer_parts(entry.filename)
                if not parsed:
                    if Path(entry.filename).suffix.lower() not in {".txt", ".cls", ".mit"}:
                        raise ValueError("nist_writer_archive_structure_changed")
                    continue
                writer_images += 1
                _, writer, form, field = parsed
                if writer not in splits or field not in {"u", "l"}:
                    continue
                checksum = sha(data)
                result = db.execute(
                    "SELECT label, ambiguous FROM classes WHERE hash=?", (checksum,)
                ).fetchone()
                if result is None or result[1] or result[0] not in LETTERS:
                    unmapped += 1
                    continue
                label = result[0]
                if (field == "u") != label.isupper():
                    unmapped += 1
                    continue
                with Image.open(io.BytesIO(data)) as image:
                    if (
                        image.format != "PNG"
                        or image.size != (128, 128)
                        or getattr(image, "n_frames", 1) != 1
                    ):
                        raise ValueError("nist_selected_image_format_or_dimensions_changed")
                    image.load()
                    gray = image.convert("L")
                    if gray.getextrema()[0] == gray.getextrema()[1]:
                        blank += 1
                        continue
                    pixel_hash = sha(gray.tobytes())
                path = Path("images") / (checksum + ".png")
                target = temporary / path
                target.parent.mkdir(exist_ok=True, mode=0o700)
                if not target.exists():
                    with target.open("xb") as output:
                        output.write(data)
                rows.append(
                    {
                        "id": "r3-" + sha(entry.filename.encode())[:24],
                        "hash": checksum,
                        "path": path.as_posix(),
                        "label": label,
                        "writer": "nist-" + sha(writer.encode())[:16],
                        "split": splits[writer],
                        "pixel_hash": pixel_hash,
                        "source_group": "nist-page-" + sha((writer + ":" + form).encode())[:24],
                        "source_member": entry.filename,
                    }
                )
        selected, filtering = select(rows, caps, seed)
        candidates = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        (temporary / "candidates-private.jsonl").write_text(candidates)
        mapping = "".join(json.dumps(row, sort_keys=True) + "\n" for row in selected)
        (temporary / "study.jsonl").write_text(mapping)
        report = {
            "version": "nist-letter-preparation-r3-v1",
            "status": "prepared_pending_curator_admission",
            "seed": seed,
            "per_class_caps": caps,
            "candidate_writer_limits": {"train": 128, "validation": 16, "test": 16},
            "candidate_mapping_sha256": sha(candidates.encode()),
            "sources": receipts,
            "preparation_code_sha256": {
                name: sha((ROOT / name).read_bytes())
                for name in (
                    "research/english/acquire.py",
                    "research/english/nist.py",
                    "research/english/check.py",
                    "research/preparation/archive_audit.py",
                    "research/fetch_deva_datasets.py",
                    "pyproject.toml",
                    "uv.lock",
                )
            },
            "environment": {"python": sys.version, "pillow": pillow_version},
            "class_images_crc_checked": class_images,
            "writer_images_crc_checked": writer_images,
            "source_writers": len(writers),
            "unmapped_or_ambiguous_candidate_images": unmapped,
            "blank_candidate_images": blank,
            **filtering,
            "private_mapping_sha256": sha(mapping.encode()),
            "partitions": {
                split: {
                    "samples": sum(r["split"] == split for r in selected),
                    "writers": len({r["writer"] for r in selected if r["split"] == split}),
                    "per_letter": dict(
                        sorted(
                            Counter(
                                f"U+{ord(r['label']):04X}" for r in selected if r["split"] == split
                            ).items()
                        )
                    ),
                }
                for split in caps
            },
            "case_preserved": True,
            "writer_disjoint": True,
            "all_archive_members_crc_checked": True,
            "selected_images_decoded": True,
            "duplicate_scope": "exact_byte_and_exact_grayscale_pixel_identity_in_candidate_pool",
            "nonexact_duplicate_search": "not_performed_requires_curator_review",
            "training": "not_run",
            "model_inference": False,
        }
        (temporary / "preparation.json").write_text(json.dumps(report, indent=2) + "\n")
        db.close()
        if destination.exists():
            raise ValueError("nist_staging_created_concurrently")
        temporary.rename(destination)
        return report
    finally:
        db.close()
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        report = audit(args.sources, args.destination)
    except LetterCoverageError as error:
        print(json.dumps(error.report, indent=2))
        raise SystemExit(2) from None
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
