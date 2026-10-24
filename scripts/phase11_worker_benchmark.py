"""Compile AND validate varied synthetic outlines under a sampled process-tree cap.

CI image smoke additionally enforces a container limit. Local measurements establish
engineering cost, never handwriting quality or hosted cost.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / path)
    for path in ("apps/api", "packages/contracts", "packages/font-engine", "tests/backend", ".")
]

from font_fixtures import fixture_manifest  # noqa: E402
from penforge_font_engine.compiler import compile_font, installed_versions  # noqa: E402
from penforge_font_engine.validation import LAYERS, validate_font  # noqa: E402

from scripts.benchmark_resources import tree_rss_bytes  # noqa: E402
from scripts.phase10_spacing_eval import varied  # noqa: E402
from scripts.phase11_pg_benchmark import stats  # noqa: E402

OUTPUT = ROOT / f"research/evaluation/benchmarks/worker-local-{datetime.now(UTC):%Y%m%d}.json"


def run(format: str, runs: int = 3) -> dict:
    rows = varied()
    manifest = fixture_manifest(options={"spacing": 50})
    compiles, validations, totals, statuses = [], [], [], []
    with tempfile.TemporaryDirectory(prefix="penforge-benchmark-") as temporary:
        for index in range(runs):
            started = time.perf_counter()
            data = compile_font(manifest, rows, format)
            compiled = time.perf_counter()
            report = validate_font(data, manifest, rows, format, Path(temporary) / str(index))
            completed = time.perf_counter()
            compiles.append((compiled - started) * 1000)
            validations.append((completed - compiled) * 1000)
            totals.append((completed - started) * 1000)
            statuses.append(report["status"])
            if set(report["layers"]) != set(LAYERS) or any(
                layer["status"] != "passed" for layer in report["layers"].values()
            ):
                raise ValueError("required_validation_layer_failed")
    return dict(
        format=format,
        samples=runs,
        compile_ms=stats(compiles),
        validation_ms=stats(validations),
        total_ms=stats(totals),
        raw_ms=dict(compile=compiles, validation=validations, total=totals),
        bytes=len(data),
        font_sha256="sha256:" + hashlib.sha256(data).hexdigest(),
        corrected_pairs=report["coverage"]["spacing"]["corrected_pairs"],
        validation_statuses=statuses,
        validation_layers={key: value["status"] for key, value in report["layers"].items()},
    )


def finish_report(report: dict, output: Path) -> int:
    passed = (
        report.get("capture", {}).get("status") == "passed"
        and not report.get("error")
        and report.get("peak_process_tree_rss_bytes", 0) > 0
        and {row["format"] for row in report.get("results", [])} == {"ttf", "otf", "woff2"}
        and all(
            set(row["validation_layers"]) == set(LAYERS)
            and all(value == "passed" for value in row["validation_layers"].values())
            and all(
                value in {"passed", "passed_with_warnings"} for value in row["validation_statuses"]
            )
            and row["corrected_pairs"] > 0
            for row in report["results"]
        )
    )
    report["status"] = "passed" if passed else "failed"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(output), "status": report["status"]}))
    return 0 if passed else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--memory-mb", type=int, default=768)
    args = parser.parse_args(argv)
    if args.samples < 1 or args.memory_mb < 32:
        parser.error("positive sample count and at least 32 MiB required")
    if args.child:
        from scripts.benchmark_capture import run_capture

        capture = run_capture(args.samples)
        results = [run(format, args.samples) for format in ("ttf", "otf", "woff2")]
        cpu = sum(
            resource.getrusage(who).ru_utime + resource.getrusage(who).ru_stime
            for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)
        )
        print(json.dumps(dict(results=results, capture=capture, cpu_seconds=cpu)))
        return 0
    report = dict(
        schema="phase11.worker-benchmark.v2",
        date=datetime.now(UTC).isoformat(),
        provider="local",
        stage="verify_extract_compile_validate",
        fixture="synthetic_varied_95_glyphs",
        tool_versions=installed_versions(),
        host=dict(platform=platform.platform(), python=platform.python_version()),
        peak_process_tree_rss_bytes=0,
        memory_limit_bytes=args.memory_mb * 1024**2,
        sampling_interval_seconds=0.02,
        results=[],
    )
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                "--samples",
                str(args.samples),
            ],
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            env=dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1"),
        )
        try:
            while process.poll() is None:
                peak = max(report["peak_process_tree_rss_bytes"], tree_rss_bytes(process.pid))
                report["peak_process_tree_rss_bytes"] = peak
                if peak > report["memory_limit_bytes"] or time.monotonic() - started > 120:
                    report["error"] = "benchmark_resource_limit"
                    break
                time.sleep(0.02)
        except (OSError, RuntimeError):
            report["error"] = "resource_monitor_unavailable"
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        stdout.seek(0)
        if process.returncode:
            report.setdefault("error", "benchmark_child_failed")
        elif not report.get("error"):
            try:
                report.update(json.load(stdout))
            except (ValueError, KeyError):
                report["error"] = "invalid_benchmark_child_report"
    report["startup_and_work_wall_seconds"] = time.monotonic() - started
    return finish_report(report, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
