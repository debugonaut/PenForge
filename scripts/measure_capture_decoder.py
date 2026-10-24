"""Reproducible synthetic decoder resource measurements, separate from ML quality."""

import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, __version__

from packages.capture_safety import DecodeLimits, verify_image


def source(directory: Path, name: str) -> tuple[Path, str]:
    path = directory / name
    if name == "ruled-12mp.jpg":
        image = Image.new("RGB", (3000, 4000), "#f8f1df")
        draw = ImageDraw.Draw(image)
        for line in range(200, 4000, 100):
            draw.line((100, line, 2900, line), fill="#b1c4d1", width=3)
        for line in range(300, 3700, 100):
            draw.text(
                (200, line - 35),
                "Synthetic capture safety fixture ABC abc 123",
                fill="#332a22",
                font_size=35,
            )
        image.save(path, quality=85)
        image.close()
        return path, "image/jpeg"
    if name == "noise-1mp.png":
        # Deterministic bytes from counter hashes, no external/private input.
        blocks = [hashlib.sha256(index.to_bytes(4, "big")).digest() for index in range(93750)]
        image = Image.frombytes("RGB", (1000, 1000), b"".join(blocks))
        image.save(path)
        image.close()
        return path, "image/png"
    if name == "oriented-alpha-50mp.png":
        image = Image.new("RGBA", (10000, 5000), (247, 238, 220, 180))
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "Synthetic metadata removal fixture"
        image.save(path, exif=exif)
        image.close()
        return path, "image/png"
    if name == "bounded-20mib.jpg":
        image = Image.new("RGB", (40, 30), "white")
        image.save(path)
        image.close()
        original = path.read_bytes()
        remaining = DecodeLimits().max_bytes - len(original)
        segments = []
        while remaining:
            segment_size = min(65537, remaining)
            if remaining - segment_size in {1, 2, 3}:
                segment_size -= 4
            if segment_size < 4:
                raise ValueError("Invalid synthetic padding")
            segments.append(
                b"\xff\xef" + (segment_size - 2).to_bytes(2, "big") + b"\0" * (segment_size - 4)
            )
            remaining -= segment_size
        path.write_bytes(original[:2] + b"".join(segments) + original[2:])
        return path, "image/jpeg"
    image = Image.new("RGB", (1600, 1200), "#f3ecd9")
    image.save(path, format="WEBP", quality=80)
    image.close()
    return path, "image/webp"


def main() -> None:
    records = []
    with tempfile.TemporaryDirectory(prefix="penforge-decoder-measure-") as directory:
        working = Path(directory)
        for name in [
            "ruled-12mp.jpg",
            "noise-1mp.png",
            "oriented-alpha-50mp.png",
            "bounded-20mib.jpg",
            "plain-1.92mp.webp",
        ]:
            path, mime = source(working, name)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            attempts = []
            for _ in range(3):
                result = verify_image(path, declared_mime=mime, output_dir=working / "out")
                attempts.append(result.metrics)
                result.derived_path.unlink()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == before
            records.append(
                {
                    "fixture": name,
                    "mime": mime,
                    "n": len(attempts),
                    "width_px": result.width_px,
                    "height_px": result.height_px,
                    "bytes_in": result.size_bytes,
                    "source_sha256": result.original_sha256,
                    "bytes_out": result.derived_size_bytes,
                    "p50_ms": statistics.median(item["wall_seconds"] for item in attempts) * 1000,
                    # With n3 the maximum is the nearest-rank p95; raw values retained.
                    "p95_ms": max(item["wall_seconds"] for item in attempts) * 1000,
                    "peak_rss_mb": max(item["peak_rss_bytes"] for item in attempts) / 1024**2,
                    "attempts": attempts,
                }
            )
    report = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "stage": "asset_verify_decoder",
        "provider": "local",
        "runtime": {
            "python": sys.version,
            "pillow": __version__,
            "os": platform.platform(),
            "architecture": platform.machine(),
            "logical_cpus": os.cpu_count(),
        },
        "limits": asdict(DecodeLimits()),
        "fixtures": records,
        "scope": "Synthetic format/security/resource fixtures only; no handwriting, recognition, extraction, style, model, or font quality measurement.",
        "limitations": [
            "n3 per fixture is a local smoke profile, not a production throughput/concurrency or ETA baseline.",
            "Darwin rejects finite AS/DATA rlimits; memory is guarded by native RSS sampling every20ms, with possible sampling overshoot. CPU and wall/file limits are enforced.",
            "Linux seccomp/AS implementation and Docker cgroup limits await Linux/container execution; no hosted provider is measured.",
        ],
    }
    target = Path("artifacts/phase-03/decoder-resources.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Measured {len(records)} synthetic fixtures; saved {target}")


if __name__ == "__main__":
    main()
