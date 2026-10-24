"""Local synthetic engineering measurements, never handwriting or cloud capacity."""

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests/backend"))
from font_fixtures import fixture_glyphs, fixture_manifest  # noqa: E402
from penforge_font_engine.isolated import compile_isolated  # noqa: E402


def main():
    out = ROOT / "artifacts/phase05-fonts"
    out.mkdir(parents=True, exist_ok=True)
    records = []
    manifest = fixture_manifest()
    (out / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n")
    for full in [False, True]:
        label = "ascii95" if full else "letters52"
        for fmt in ["ttf", "otf", "woff2"]:
            hashes = []
            for repeat in range(2):
                directory = out / f"{label}-{fmt}-run{repeat + 1}"
                font, report = compile_isolated(
                    directory,
                    manifest.model_dump(mode="json"),
                    fixture_glyphs(full, nonbmp=True),
                    fmt,
                )
                (out / f"{label}.{fmt}").write_bytes(font)
                (directory / "retained-report.json").write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n"
                )
                hashes.append(hashlib.sha256(font).hexdigest())
                records.append(
                    dict(
                        fixture=label,
                        format=fmt,
                        repeat=repeat + 1,
                        sha256=hashes[-1],
                        size_bytes=len(font),
                        status=report["status"],
                        layers={k: v["status"] for k, v in report["layers"].items()},
                        resources=report["resources"],
                    )
                )
                if report["status"] == "failed":
                    raise RuntimeError("required_validator_failed")
            if len(set(hashes)) != 1:
                raise RuntimeError("reproducibility_failed")
    hardware = dict(
        platform=platform.platform(),
        machine=platform.machine(),
        python=platform.python_version(),
        cpu=subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
        ).stdout.strip(),
        memory_bytes=subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True
        ).stdout.strip(),
    )
    summary = dict(
        schema_version=1,
        synthetic_only=True,
        real_handwriting_accuracy=None,
        cloud_capacity=None,
        tool_versions=manifest.tool_versions,
        build_id=manifest.build_id,
        hardware=hardware,
        records=records,
    )
    (out / "measurements.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(
        f"{len(records)} isolated synthetic builds passed all five layers; repeated hashes match."
    )


if __name__ == "__main__":
    main()
