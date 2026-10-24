"""Fetch and verify the Devanagari handwriting archives named in research/manifests.

Raw archives are never committed. They land in ``$PENFORGE_DATA`` (default
``~/PenForge-data``) and the manifests record their SHA-256 once verified.

Usage:
    python3 research/fetch_deva_datasets.py --list
    python3 research/fetch_deva_datasets.py --fetch all
    python3 research/fetch_deva_datasets.py --verify
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DATA_ROOT = Path(os.environ.get("PENFORGE_DATA", Path.home() / "PenForge-data"))


@dataclass(frozen=True)
class Source:
    key: str
    manifest: str
    directory: str
    filename: str
    url: str
    expected_bytes: int


SOURCES: tuple[Source, ...] = (
    Source(
        key="iiit-hw-dev",
        manifest="iiit-hw-dev-v1",
        directory="iiit",
        filename="IIIT-HW-Dev.zip",
        url="https://cvit.iiit.ac.in/images/Projects/wordlevel-Indicscripts/IIIT-HW-Dev.zip",
        expected_bytes=1920697925,
    ),
    Source(
        key="iiit-hw-hindi-v1",
        manifest="iiit-indic-hw-words-v1",
        directory="iiit",
        filename="IIIT-HW-Hindi_v1.tar.gz",
        url="https://cvit.iiit.ac.in/images/Projects/wordlevel-Indicscripts/IIIT-HW-Hindi_v1.tar.gz",
        expected_bytes=1822862305,
    ),
    Source(
        key="iiit-indic-hw-uc-hindi",
        manifest="iiit-indic-hw-uc-v1",
        directory="iiit",
        filename="indic-hindi.zip",
        url="https://cvit.iiit.ac.in/images/datasets/indic_handwritten/hindi.zip",
        expected_bytes=3576032718,
    ),
    Source(
        key="iiit-indic-hw-uc-marathi",
        manifest="iiit-indic-hw-uc-v1",
        directory="iiit",
        filename="indic-marathi.zip",
        url="https://cvit.iiit.ac.in/images/datasets/indic_handwritten/marathi.zip",
        expected_bytes=3009421257,
    ),
    Source(
        key="dhcd",
        manifest="dhcd-candidate-v1",
        directory="deva",
        filename="dhcd.zip",
        url="https://archive.ics.uci.edu/static/public/389/devanagari+handwritten+character+dataset.zip",
        expected_bytes=80417803,
    ),
)


def target(source: Source) -> Path:
    return DATA_ROOT / source.directory / source.filename


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(source: Source) -> None:
    path = target(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    offset = path.stat().st_size if path.exists() else 0
    if offset == source.expected_bytes:
        print(f"{source.key}: already complete ({offset} bytes)")
        return
    request = urllib.request.Request(
        source.url, headers={"Range": f"bytes={offset}-"} if offset else {}
    )
    with (
        urllib.request.urlopen(request, timeout=120) as response,
        path.open("ab" if offset else "wb") as handle,
    ):
        while chunk := response.read(1 << 20):
            handle.write(chunk)
    print(f"{source.key}: {path} {path.stat().st_size} bytes")


def verify() -> int:
    failures = 0
    for source in SOURCES:
        path = target(source)
        if not path.exists():
            print(f"MISSING  {source.key}: {path}")
            failures += 1
            continue
        size = path.stat().st_size
        if size != source.expected_bytes:
            print(f"PARTIAL  {source.key}: {size} of {source.expected_bytes} bytes")
            failures += 1
            continue
        print(f"OK       {source.key}: sha256:{sha256(path)}")
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print sources and destination paths")
    parser.add_argument("--fetch", metavar="KEY", help="fetch one key or 'all'")
    parser.add_argument("--verify", action="store_true", help="check sizes and print SHA-256")
    args = parser.parse_args(argv)

    if args.list:
        for source in SOURCES:
            print(f"{source.key:24} {source.expected_bytes:>12} -> {target(source)}")
        return 0
    if args.fetch:
        selected = (
            SOURCES
            if args.fetch == "all"
            else tuple(source for source in SOURCES if source.key == args.fetch)
        )
        if not selected:
            print(f"unknown key: {args.fetch}", file=sys.stderr)
            return 2
        for source in selected:
            fetch(source)
        return 0
    if args.verify:
        return 1 if verify() else 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
