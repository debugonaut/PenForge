"""Local synthetic engineering measurements; never a handwriting-quality benchmark."""

import hashlib
import json
import platform
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT),
    str(ROOT / "apps/api"),
    str(ROOT / "packages/contracts"),
    str(ROOT / "packages/font-engine"),
    str(ROOT / "tests/backend"),
]

from app.modules.glyphs.style_measurements import measure  # noqa: E402
from font_fixtures import fixture_manifest  # noqa: E402
from penforge_font_engine.isolated import compile_isolated  # noqa: E402
from test_contextual_cycling import fixture  # noqa: E402


def style_control():
    outline = json.loads((ROOT / "packages/contracts/fixtures/outline.v1.json").read_text())
    refs = []
    for cp in (65, 97, 98, 101, 103, 120):
        raw = json.loads(json.dumps(outline))
        raw["codepoints"] = [cp]
        refs.append(
            {
                "codepoints": [cp],
                "variant_index": 0,
                "glyph_id": f"control-{cp}",
                "revision_id": f"revision-{cp}",
                "revision_hash": "sha256:" + hashlib.sha256(str(cp).encode()).hexdigest(),
                "outline": raw,
            }
        )
    started = time.perf_counter()
    body, digest, canonical = measure("sha256:" + "a" * 64, refs)
    wall = time.perf_counter() - started
    return (
        body,
        digest,
        canonical,
        {
            "wall_seconds": wall,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
        },
    )


def main():
    if "--style-child" in sys.argv:
        _, digest, _, observed = style_control()
        print(json.dumps({"profile_hash": digest, **observed}))
        return
    out = ROOT / "artifacts/phase08/measurements"
    if out.exists():
        raise SystemExit("Keep existing evidence; choose a new run directory.")
    out.mkdir(parents=True)
    body, digest, canonical, _ = style_control()
    (out / "style-profile.json").write_text(json.dumps(body, indent=2))
    (out / "style-profile.canonical.json").write_bytes(canonical)
    repeats = []
    for _ in range(3):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--style-child"],
            check=True,
            capture_output=True,
            text=True,
        )
        repeats.append(json.loads(result.stdout))
    assert {r["profile_hash"] for r in repeats} == {digest}
    fonts = []
    for n in (2, 3):
        manifest = fixture_manifest(options={"realism": 50 if n == 2 else 100})
        for fmt in ("ttf", "otf", "woff2"):
            with tempfile.TemporaryDirectory(prefix="penforge-phase08-measure-") as working:
                data, report = compile_isolated(
                    Path(working), manifest.model_dump(mode="json"), fixture(n), fmt
                )
                assert report["status"] == "passed_with_warnings"
                folder = out / f"n{n}-{fmt}"
                folder.mkdir()
                (folder / f"fixture.{fmt}").write_bytes(data)
                (folder / "report.json").write_text(json.dumps(report, indent=2))
                fonts.append(
                    {
                        "set_size": n,
                        "format": fmt,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "resources": report["resources"],
                        "isolation": report["isolation"],
                    }
                )
    cpu = (
        subprocess.run(
            ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
        ).stdout.strip()
        if sys.platform == "darwin"
        else platform.processor()
    )
    memory = (
        subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True, text=True
        ).stdout.strip()
        if sys.platform == "darwin"
        else None
    )
    summary = {
        "domain": "synthetic_engineering_controls_only",
        "hardware": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "cpu": cpu,
            "memory_bytes": int(memory) if memory else None,
            "python": platform.python_version(),
        },
        "profile_hash": digest,
        "canonical_byte_sha256": hashlib.sha256(canonical).hexdigest(),
        "style_repeats": repeats,
        "fonts": fonts,
        "lock_hash": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "pid_scope": "each style repeat is a new process; each font report measures its isolated child",
        "handwriting_quality": None,
        "writer_fidelity": None,
        "human_preference": None,
        "provider_capacity": None,
        "eta": None,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(
        json.dumps(
            {"style_repeats": 3, "isolated_font_runs": len(fonts), "domain": summary["domain"]}
        )
    )


if __name__ == "__main__":
    main()
