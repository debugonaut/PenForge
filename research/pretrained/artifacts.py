"""Download/verify an allowlisted model bundle. Never imports a model framework."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "microsoft/trocr-base-handwritten"
REVISION = "eaacaf452b06415df8f10bb6fad3a4c11e609406"
# Public Hub metadata, inspected 2026-10-21. The weights use the upstream LFS SHA-256.
# Small files were downloaded as metadata only, checked against upstream Git blob
# IDs, then SHA-256 pinned here. No weight download was needed to establish pins.
FILES = {
    "README.md": (2999, "9f808e1fc12dcb61c89a38f00303506dbc85db8ea988cec441a9324e0c8f2843"),
    "config.json": (4165, "4c779f24e063c437c3dafd5b2e6c9f59f2fa2bd1dbb4ae6a30153bbbbf19e647"),
    "generation_config.json": (
        190,
        "41149cdcffec4d657f32dfcddd9b208037f01286c9e07945c724908c58ed0193",
    ),
    "merges.txt": (456318, "1ce1664773c50f3e0cc8842619a93edc4624525b728b188a9e0be33b7726adc5"),
    "model.safetensors": (
        1333384464,
        "25a40cddc7e6120140a3d5b9e3dd3878a92ada7b4f312953ab22edc19c2a5acc",
    ),
    "preprocessor_config.json": (
        224,
        "2fcc0da9466ee00be0403b26027373039e032820ebac409e207b32e52e52119d",
    ),
    "special_tokens_map.json": (
        772,
        "c611b1f7d416eb001ee4f293d903ea8c88e703463f1d403f1866a0352743fd00",
    ),
    "tokenizer_config.json": (
        1118,
        "5a1356884c6ae736a621841535264ba7c5bebd52f169258add2c48fcbb32d50a",
    ),
    "vocab.json": (898822, "06b4d46c8e752d410213d9548eb27a54db70fda0319b6271fb8d59dead5e1cab"),
}
NOTICE = """The MIT License (MIT)

Copyright (c) Microsoft Corporation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def digest_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def private_destination(path: Path) -> Path:
    path = path.resolve()
    if path == ROOT or path.is_relative_to(ROOT):
        raise ValueError("model_bundle_must_be_outside_repository")
    return path


def inspect_file(path: Path, expected: tuple[int, str]) -> dict:
    size, expected_hash = expected
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        raise ValueError("model_file_missing_symlink_or_wrong_size")
    sha256 = hashlib.sha256()
    git_blob = hashlib.sha1(b"blob " + str(size).encode() + b"\0")
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            sha256.update(chunk)
            git_blob.update(chunk)
    actual = sha256.hexdigest() if len(expected_hash) == 64 else git_blob.hexdigest()
    if actual != expected_hash:
        raise ValueError("model_file_checksum_mismatch")
    return {"bytes": size, "sha256": sha256.hexdigest()}


def verify_bundle(path: Path, *, metadata_only: bool = False) -> dict:
    path = private_destination(path)
    selected = {k: v for k, v in FILES.items() if not metadata_only or k != "model.safetensors"}
    expected = set(selected) | {"receipt.json", "LICENSE-Microsoft.txt"}
    if {p.name for p in path.iterdir()} != expected:
        raise ValueError("model_bundle_unexpected_or_missing_files")
    for name in expected:
        if (path / name).is_symlink() or not (path / name).is_file():
            raise ValueError("model_bundle_requires_regular_files")
    if (path / "LICENSE-Microsoft.txt").read_text() != NOTICE:
        raise ValueError("model_notice_changed")
    files = {name: inspect_file(path / name, spec) for name, spec in selected.items()}
    receipt = json.loads((path / "receipt.json").read_text())
    if receipt != {
        "model_id": MODEL_ID,
        "revision": REVISION,
        "files": files,
        "scope": "metadata_only" if metadata_only else "complete",
    }:
        raise ValueError("model_receipt_changed")
    config = json.loads((path / "config.json").read_text())
    if (
        config.get("model_type") != "vision-encoder-decoder"
        or config.get("encoder", {}).get("model_type") != "vit"
        or config.get("decoder", {}).get("model_type") != "trocr"
        or "auto_map" in config
    ):
        raise ValueError("unexpected_model_architecture")
    return receipt


def download(path: Path, *, metadata_only: bool = False) -> dict:
    path = private_destination(path)
    if path.exists():
        raise ValueError("model_destination_already_exists_verify_or_choose_new_directory")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".penforge-trocr-", dir=path.parent))
    try:
        receipt: dict = {
            "model_id": MODEL_ID,
            "revision": REVISION,
            "files": {},
            "scope": "metadata_only" if metadata_only else "complete",
        }
        for name, spec in FILES.items():
            if metadata_only and name == "model.safetensors":
                continue
            url = f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}"
            target = temporary / name
            # Fixed HTTPS URLs only; no client-supplied URL or model-hub code.
            with urllib.request.urlopen(url, timeout=60) as source, target.open("xb") as output:
                total = 0
                for chunk in iter(lambda: source.read(1 << 20), b""):
                    total += len(chunk)
                    if total > spec[0]:
                        raise ValueError("model_download_size_limit")
                    output.write(chunk)
            os.chmod(target, 0o600)
            receipt["files"][name] = inspect_file(target, spec)
            print(json.dumps({"verified_file": name, "bytes": spec[0]}), flush=True)
        (temporary / "LICENSE-Microsoft.txt").write_text(NOTICE)
        (temporary / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        verify_bundle(temporary, metadata_only=metadata_only)
        # Do not overwrite an existing download, including an empty directory.
        if path.exists():
            raise ValueError("model_destination_created_concurrently")
        temporary.rename(path)
        return receipt
    finally:
        if temporary.exists():
            # Only the exact mkdtemp directory created by this invocation is removed.
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--metadata-only", action="store_true", help="Exclude all model weights")
    args = parser.parse_args()
    os.umask(0o077)
    result = (
        download(args.destination, metadata_only=args.metadata_only)
        if args.download
        else verify_bundle(args.destination, metadata_only=args.metadata_only)
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
