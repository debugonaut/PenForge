"""Verify the configured private bucket with the production storage adapter.

Reads `infrastructure/deployment/beta/.env.beta` (never prints values), uploads a
small probe object through the same capability path the API uses, reads it back,
lists it and removes it. Exit code 0 means the bucket, keys and region are usable.

    .venv/bin/python infrastructure/deployment/beta/check_storage.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.adapters.storage.s3 import S3ObjectStorage  # noqa: E402


def load_env(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or "REPLACE" in value or "<" in value:
        raise SystemExit(f"{name} is not configured in .env.beta")
    return value


def main() -> int:
    env_file = Path(__file__).with_name(".env.beta")
    if not env_file.exists():
        raise SystemExit(f"missing {env_file}")
    load_env(env_file)

    region = os.environ.get("STORAGE_S3_REGION", "auto").strip()
    if region in ("", "CHANGE_ME"):
        raise SystemExit("STORAGE_S3_REGION is not configured in .env.beta")

    storage = S3ObjectStorage(
        endpoint=required("STORAGE_S3_ENDPOINT"),
        bucket=required("STORAGE_S3_BUCKET"),
        region=region,
        access_key=required("STORAGE_S3_ACCESS_KEY"),
        secret_key=required("STORAGE_S3_SECRET_KEY"),
        prefix=os.environ.get("STORAGE_S3_PREFIX", ""),
        force_path_style=os.environ.get("STORAGE_S3_PATH_STYLE", "true").lower()
        in ("1", "true", "yes"),
    )
    probes = (("tiny", 41), ("page", 200 * 1024), ("capture", 2 * 1024 * 1024))
    print(f"bucket {os.environ['STORAGE_S3_BUCKET']} — real probe objects, incompressible\n")
    print(f"{'payload':8} {'bytes':>9} {'upload':>9} {'MB/s':>7} {'download':>10} {'MB/s':>7}")
    for label, size in probes:
        key = f"beta-check/{uuid.uuid4().hex}.bin"
        payload = os.urandom(size)
        digest = hashlib.sha256(payload).hexdigest()

        started = time.perf_counter()
        capability = storage.create_upload(key, size_bytes=size, sha256=digest)
        stored = storage.put(capability, payload)
        upload_ms = (time.perf_counter() - started) * 1000
        if stored.size_bytes != size or stored.sha256 != digest:
            raise SystemExit(f"stored metadata mismatch: {stored.size_bytes} bytes, {stored.sha256}")

        started = time.perf_counter()
        url = storage.get_download_url(key, ttl_seconds=60)
        returned = storage.read(url)
        download_ms = (time.perf_counter() - started) * 1000
        if returned != payload:
            raise SystemExit("downloaded bytes differ from what was uploaded")

        listed = [item.key for item in storage.list_objects(prefix="beta-check/")]
        if key not in listed:
            raise SystemExit("probe object missing from the private inventory")
        storage.delete_object(key)
        remaining = [item.key for item in storage.list_objects(prefix="beta-check/") if item.key == key]
        if remaining:
            raise SystemExit("probe object survived deletion")

        seconds_up = max(upload_ms, 0.001) / 1000
        seconds_down = max(download_ms, 0.001) / 1000
        print(
            f"{label:8} {size:>9} {upload_ms:>7.0f}ms {size / 1e6 / seconds_up:>7.2f} "
            f"{download_ms:>8.0f}ms {size / 1e6 / seconds_down:>7.2f}"
        )

    storage.close()
    print("\nstorage OK — capabilities, checksums, inventory and deletion all behave")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
