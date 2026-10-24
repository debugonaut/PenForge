"""Acquire official NIST source bytes only. Never loads or executes a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

from research.preparation.archive_audit import external

# Single-part S3 ETags and lengths observed at the URLs linked by NIST, 2026-10-21.
# These check bytes against observed publisher ETags; they are not a signed release.
# A separate SHA-256 receipt freezes every acquired artifact locally.
SOURCES = {
    "by_write.zip": (568113446, "a29f21babf83db0bb28a2f77b2b456cb"),
    "by_class.zip": (1031576378, "79572b1694a8506f2b722c7be54130c4"),
    "by_write_md5.log": (89568050, "5f5a34e8512f2fa8dd0b03cb259215a1"),
    "by_class_md5.log": (146649644, "e24ca06edb55c6a67a46d88734bce6e0"),
}
BASE = "https://s3.amazonaws.com/nist-srd/SD19/"


def inspect(path: Path, size: int, md5: str) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        raise ValueError("source_missing_symlink_or_wrong_size")
    sha, legacy = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            sha.update(chunk)
            legacy.update(chunk)
    if legacy.hexdigest() != md5:
        raise ValueError("source_publisher_checksum_mismatch")
    return {"bytes": size, "md5": md5, "sha256": sha.hexdigest()}


def acquire(folder: Path, name: str) -> dict:
    folder = external(folder)
    size, md5 = SOURCES[name]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / name
    receipt_path = folder / (name + ".receipt.json")
    url = BASE + name
    if not target.exists():
        if receipt_path.exists():
            raise ValueError("existing_receipt_without_source_refusing_replacement")
        descriptor, temporary_name = tempfile.mkstemp(prefix=".nist-", dir=folder)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                with urllib.request.urlopen(url, timeout=60) as source:
                    if source.headers.get("ETag", "").strip('"') != md5:
                        raise ValueError("nist_source_etag_changed")
                    if source.headers.get("Content-Length") != str(size):
                        raise ValueError("nist_source_length_changed")
                    received, announced = 0, 0
                    for chunk in iter(lambda: source.read(1 << 20), b""):
                        received += len(chunk)
                        if received > size:
                            raise ValueError("nist_download_size_ceiling")
                        output.write(chunk)
                        if received - announced >= 32 << 20:
                            print(
                                json.dumps({"file": name, "bytes": received, "total": size}),
                                flush=True,
                            )
                            announced = received
            inspect(temporary, size, md5)
            # Link publishes exclusively, even if another process creates target.
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    receipt = {"url": url, **inspect(target, size, md5)}
    if receipt_path.exists():
        if json.loads(receipt_path.read_text()) != receipt:
            raise ValueError("nist_source_receipt_changed")
    else:
        with receipt_path.open("x") as output:
            output.write(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--source", choices=[*SOURCES, "all"], default="all")
    args = parser.parse_args()
    os.umask(0o077)
    for name in SOURCES if args.source == "all" else [args.source]:
        print(json.dumps({"verified_source": name, **acquire(args.directory, name)}), flush=True)


if __name__ == "__main__":
    main()
