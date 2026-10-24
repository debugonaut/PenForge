"""Audit existing corpora, with all raw material outside the source checkout.

No downloads, extractall, executable deserialization, or product storage access.
The private SQLite ledger retains original paths/labels; public reports contain
hashes and counts only. ZIP reads verify CRC; exhausting TAR gzip verifies its CRC.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import resource
import sqlite3
import stat
import tarfile
import time
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

import cv2
import numpy as np
from PIL import Image

from research.fetch_deva_datasets import DATA_ROOT, SOURCES

ROOT = Path(__file__).resolve().parents[2]
VERSION = "archive-audit-r1"
MAX_ENTRIES = 1_000_000
MAX_TOTAL = 16_000_000_000
MAX_MEMBER = 128_000_000
MAX_CONTAINER = 3_000_000_000
Image.MAX_IMAGE_PIXELS = 50_000_000


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_gzip(path: Path):
    """tarfile's streaming gzip reader does not authenticate the gzip trailer."""
    total = 0
    with gzip.open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            total += len(chunk)
            if total > MAX_TOTAL:
                raise ValueError("gzip_expansion_limit")
    return total


def safe_name(name: str) -> str:
    p = PurePosixPath(name)
    if (
        not name
        or len(name) > 1024
        or p.is_absolute()
        or ".." in p.parts
        or "\\" in name
        or ":" in name
        or "\x00" in name
    ):
        raise ValueError("unsafe_archive_path")
    return str(p)


def external(path: Path) -> Path:
    path = path.resolve()
    if path == ROOT or path.is_relative_to(ROOT):
        raise ValueError("raw_staging_must_be_outside_repository")
    return path


def raster_features(data: bytes):
    with Image.open(io.BytesIO(data)) as im:
        if (
            im.format not in {"JPEG", "PNG"}
            or im.width * im.height > 50_000_000
            or max(im.size) > 12000
            or getattr(im, "n_frames", 1) != 1
        ):
            raise ValueError("image_limits_or_format")
        im.load()
        gray = np.asarray(im.convert("L"))
        width, height, fmt = im.width, im.height, im.format
    # Comparison-only normalization. Original files remain byte-identical.
    small = cv2.resize(gray, (128, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(cv2.resize(gray, (32, 32)).astype(np.float32))[:8, :8]
    bits = np.packbits((dct > np.median(dct.flat[1:])).reshape(-1)).tobytes().hex()
    return width, height, fmt, sha(small.tobytes()), bits


class Audit:
    def __init__(self, staging: Path):
        self.staging = external(staging)
        self.staging.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.staging / "inventory.sqlite")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS entries (
            corpus TEXT, path TEXT, size INTEGER, hash TEXT, kind TEXT,
            width INTEGER, height INTEGER, normalized_hash TEXT, phash TEXT,
            error TEXT, PRIMARY KEY(corpus,path));
          CREATE TABLE IF NOT EXISTS labels (
            corpus TEXT, path TEXT, label TEXT, writer TEXT, page TEXT,
            original_split TEXT, PRIMARY KEY(corpus,path));
        """)
        self.reports: dict[str, Any] = {}

    def consume(self, corpus, name, data, save=False):
        error, width, height, norm, phash = None, None, None, None, None
        kind = Path(name).suffix.lower()
        if kind in {".jpg", ".jpeg", ".png"}:
            try:
                width, height, fmt, norm, phash = raster_features(data)
                if fmt != ("PNG" if kind == ".png" else "JPEG"):
                    raise ValueError("extension_magic_mismatch")
            except ValueError as exc:
                error = (
                    str(exc)
                    if str(exc) in {"image_limits_or_format", "extension_magic_mismatch"}
                    else "unreadable_image"
                )
            except (OSError, Image.DecompressionBombError):
                error = "unreadable_or_over_budget"
        self.db.execute(
            "INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?,?,?)",
            (corpus, name, len(data), sha(data), kind, width, height, norm, phash, error),
        )
        match = re.fullmatch(r"(hindi|marathi)/file/(train|val|test)_gt.txt", name)
        if match:
            for line in data.decode("utf-8-sig").splitlines():
                path, label = line.split("\t", 1)
                path = safe_name(match[1] + "/" + path)
                group = re.search(r"word_images/([^/]+)/image/([^/_]+)_", path)
                # These are observed collection folders, NOT adjudicated identities.
                writer = "collection-" + group[1] if group else "unresolved"
                page = group[2] if group else "unresolved"
                self.db.execute(
                    "INSERT INTO labels VALUES (?,?,?,?,?,?)",
                    (corpus, path, label, writer, page, match[2]),
                )
        if corpus == "iiit-hw-hindi-v1" and name in {"train.txt", "val.txt", "test.txt"}:
            for line in data.decode("utf-8-sig").splitlines():
                path, label = line.split(" ", 1)
                parts = safe_name(path).split("/")
                self.db.execute(
                    "INSERT INTO labels VALUES (?,?,?,?,?,?)",
                    (corpus + "-inner", path, label, parts[2], parts[3], parts[1]),
                )
        if save:
            dest = self.staging / corpus / safe_name(name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                if file_hash(dest) != sha(data):
                    raise ValueError("staged_content_changed")
            else:
                dest.write_bytes(data)

    def read_zip(self, path: Path, corpus: str, save=False):
        names, total, count = set(), 0, 0
        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValueError("entry_limit")
            for info in entries:
                name = safe_name(info.filename)
                mode = info.external_attr >> 16
                if name in names or info.flag_bits & 1 or stat.S_ISLNK(mode):
                    raise ValueError("duplicate_encrypted_or_link")
                names.add(name)
                if info.is_dir():
                    continue
                nested = name.endswith((".zip", ".tar.gz"))
                limit = MAX_CONTAINER if nested else MAX_MEMBER
                total += info.file_size
                if (
                    total > MAX_TOTAL
                    or info.file_size > limit
                    or info.file_size / max(1, info.compress_size) > 300
                ):
                    raise ValueError("archive_expansion_limit")
                if nested:
                    dest = self.staging / corpus / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    h, actual = hashlib.sha256(), 0
                    with z.open(info) as src, dest.open("wb") as out:
                        for chunk in iter(lambda: src.read(1 << 20), b""):
                            h.update(chunk)
                            out.write(chunk)
                            actual += len(chunk)
                    if actual != info.file_size:
                        raise ValueError("actual_size_mismatch")
                    self.read_zip(dest, corpus + "-inner", save=save)
                else:
                    data = z.read(info)  # Full read performs ZIP CRC validation.
                    if len(data) != info.file_size:
                        raise ValueError("actual_size_mismatch")
                    self.consume(corpus, name, data, save=save)
                count += 1
                if count % 10000 == 0:
                    self.db.commit()
                    print(json.dumps({"corpus": corpus, "verified_entries": count}), flush=True)
        self.db.commit()
        self.reports[corpus] = {
            "entries": len(entries),
            "files": count,
            "actual_bytes": total,
            "integrity": "zip_crc_full_read",
            "unsafe_entries": 0,
        }

    def read_tar(self, path: Path, corpus: str, save=False):
        names, total, count = set(), 0, 0
        with tarfile.open(path, "r|gz") as tar:
            for info in tar:
                name = safe_name(info.name)
                if name in names or not (info.isdir() or info.isfile()):
                    raise ValueError("duplicate_or_special_tar_member")
                names.add(name)
                if len(names) > MAX_ENTRIES:
                    raise ValueError("entry_limit")
                if info.isdir():
                    continue
                nested = name.endswith(".tar.gz")
                total += info.size
                if total > MAX_TOTAL or info.size > (MAX_CONTAINER if nested else MAX_MEMBER):
                    raise ValueError("archive_expansion_limit")
                src = tar.extractfile(info)
                if src is None:
                    raise ValueError("missing_tar_stream")
                if nested:
                    dest = self.staging / corpus / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    actual = 0
                    with dest.open("wb") as out:
                        for chunk in iter(lambda: src.read(1 << 20), b""):
                            out.write(chunk)
                            actual += len(chunk)
                    if actual != info.size:
                        raise ValueError("actual_size_mismatch")
                    self.read_tar(dest, corpus + "-inner", save=save)
                else:
                    data = src.read(MAX_MEMBER + 1)
                    if len(data) != info.size:
                        raise ValueError("actual_size_mismatch")
                    self.consume(corpus, name, data, save=save)
                count += 1
                if count % 10000 == 0:
                    self.db.commit()
                    print(json.dumps({"corpus": corpus, "verified_entries": count}), flush=True)
            # Drain gzip trailer: TAR's zero blocks alone need not read it.
            assert tar.fileobj is not None
            while tar.fileobj.read(1 << 20):
                pass
        self.db.commit()
        gzip_bytes = verify_gzip(path)
        self.reports[corpus] = {
            "entries": len(names),
            "files": count,
            "actual_bytes": total,
            "integrity": "tar_headers_and_independent_gzip_crc",
            "gzip_decoded_bytes": gzip_bytes,
            "unsafe_entries": 0,
        }

    def parse_dev_labels(self):
        base = self.staging / "iiit-hw-dev/IIIT-HW-Dev"
        for split in ("train", "val", "test"):
            for line in (base / f"{split}.txt").read_text().splitlines():
                path, label = line.split(" ", 1)
                safe_name(path)
                parts = path.split("/")
                if len(parts) != 5 or parts[1] != split or not parts[2].isdigit():
                    raise ValueError("unexpected_writer_layout")
                self.db.execute(
                    "INSERT INTO labels VALUES (?,?,?,?,?,?)",
                    (
                        "iiit-hw-dev-inner",
                        "HindiOp2/" + "/".join(parts[2:]),
                        label,
                        parts[2],
                        parts[3],
                        split,
                    ),
                )
        self.db.commit()

    def summary(self):
        result: dict[str, Any] = {"version": VERSION, "archives": self.reports, "corpora": {}}
        for (corpus,) in self.db.execute("SELECT DISTINCT corpus FROM entries ORDER BY corpus"):
            rows = self.db.execute(
                "SELECT path,size,hash,kind,width,height,error FROM entries "
                "WHERE corpus=? ORDER BY path",
                (corpus,),
            ).fetchall()
            h = hashlib.sha256()
            for path, size, checksum, *_ in rows:
                h.update(json.dumps([sha(path.encode()), size, checksum]).encode())
            images = [r for r in rows if r[3] in {".jpg", ".png", ".jpeg"}]
            result["corpora"][corpus] = {
                "files": len(rows),
                "images": len(images),
                "types": dict(Counter(r[3] for r in rows)),
                "image_errors": sum(r[6] is not None for r in images),
                "image_error_types": dict(Counter(r[6] for r in images if r[6])),
                "inventory_sha256": h.hexdigest(),
                "dimensions": {
                    "min_width": min((r[4] for r in images if r[4]), default=None),
                    "max_width": max((r[4] for r in images if r[4]), default=None),
                    "min_height": min((r[5] for r in images if r[5]), default=None),
                    "max_height": max((r[5] for r in images if r[5]), default=None),
                },
            }
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--corpus", choices=[s.key for s in SOURCES] + ["doha"], required=True)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    resource.setrlimit(resource.RLIMIT_CPU, (3600, 3600))
    start = time.monotonic()
    audit = Audit(args.staging)
    if args.corpus == "doha":
        paths = sorted((DATA_ROOT / "doha/images").glob("*"))
        for p in paths:
            if p.is_file():
                audit.consume("doha", p.name, p.read_bytes())
        audit.db.commit()
    else:
        source = next(s for s in SOURCES if s.key == args.corpus)
        path = DATA_ROOT / source.directory / source.filename
        if not path.exists() and source.key.startswith("iiit-indic-hw-uc"):
            path = Path.home() / "Downloads" / source.filename.replace("indic-", "")
        if not path.exists() or path.stat().st_size != source.expected_bytes:
            raise ValueError("missing_or_wrong_length_archive")
        before = path.stat()
        checksum = file_hash(path)
        print(json.dumps({"corpus": source.key, "sha256": checksum}), flush=True)
        if path.suffix == ".zip":
            audit.read_zip(path, source.key, save=source.key == "iiit-hw-dev")
        else:
            audit.read_tar(path, source.key)
        audit.reports[source.key].update(
            sha256=checksum, bytes=path.stat().st_size, expected_bytes=source.expected_bytes
        )
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("archive_changed_during_audit")
        if source.key == "iiit-hw-dev":
            audit.parse_dev_labels()
    result = audit.summary()
    result["resources"] = {
        "wall_s": time.monotonic() - start,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["resources"]), flush=True)


if __name__ == "__main__":
    main()
