"""Fixed entry point, bounded and network denied before font parsing."""

import json
import os
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "packages/contracts"), str(ROOT / "packages/font-engine")]
from penforge_font_engine._sandbox import install_and_check_network_deny  # noqa: E402


def main():
    isolation = install_and_check_network_deny()
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    from penforge_contracts import FontBuildManifest

    from penforge_font_engine.compiler import compile_font
    from penforge_font_engine.validation import validate_font

    started = time.monotonic()
    config = json.loads(Path("input.json").read_bytes())
    manifest = FontBuildManifest.model_validate(config["manifest"])

    def progress(completed):
        Path("progress.tmp").write_text(
            json.dumps(dict(stage="validating", completed_units=completed, total_units=5))
        )
        os.replace("progress.tmp", "progress.json")

    compile_started = time.monotonic()
    data = compile_font(manifest, config["glyphs"], config["format"])
    compile_seconds = time.monotonic() - compile_started
    Path("font").write_bytes(data)
    progress(0)
    report = validate_font(
        data,
        manifest,
        config["glyphs"],
        config["format"],
        Path.cwd() / "validation",
        layer_completed=progress,
    )
    unit = 1 if sys.platform == "darwin" else 1024
    report["resources"] = dict(
        compile_seconds=compile_seconds,
        validation_seconds=time.monotonic() - compile_started - compile_seconds,
        wall_seconds=time.monotonic() - started,
        peak_rss_bytes=max(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        )
        * unit,
    )
    report["isolation"] = isolation
    Path("report.json").write_text(json.dumps(report, sort_keys=True, separators=(",", ":")))
    print('{"ok":true}', flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(1) from None
