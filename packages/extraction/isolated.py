"""Bound the entire analysis parser/vectorizer, reusing Phase 03 OS network isolation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from packages.capture_safety.decoder import DecoderUnavailable, ImageRejected, _kill, _rss_bytes


def analyze_isolated(
    working: Path,
    config: dict[str, Any],
    *,
    cancel_check: Callable[[], None],
    memory_mb: int = 768,
    cpu_seconds: int = 20,
    wall_seconds: float = 30,
) -> dict:
    if _rss_bytes(os.getpid()) <= 0:
        raise DecoderUnavailable("memory_monitor_unavailable")
    command = [sys.executable, "-I", str(Path(__file__).with_name("_analysis_child.py"))]
    if sys.platform == "darwin":
        command = [
            "/usr/bin/sandbox-exec",
            "-p",
            "(version 1)(allow default)(deny network*)",
            *command,
        ]
    elif sys.platform != "linux":
        raise DecoderUnavailable()
    config = dict(config, cpu_seconds=cpu_seconds)
    started, peak = time.monotonic(), 0
    with (working / "stdout").open("w+b") as stdout, (working / "stderr").open("w+b") as stderr:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            cwd=working,
            close_fds=True,
            start_new_session=True,
            env={
                "PATH": os.defpath,
                "LANG": "C",
                "TMPDIR": str(working),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps(config).encode())
        process.stdin.close()
        try:
            while process.poll() is None:
                cancel_check()
                peak = max(peak, _rss_bytes(process.pid))
                if peak > memory_mb * 1024 * 1024 or time.monotonic() - started > wall_seconds:
                    raise ImageRejected("analysis_resource_limit")
                time.sleep(0.02)
        finally:
            if process.poll() is None:
                _kill(process)
        stdout.seek(0)
        if process.returncode or stdout.read(1025) != b'{"ok":true}\n':
            raise DecoderUnavailable("analysis_child_unavailable")
    index = working / "result.json"
    if index.is_symlink() or index.stat().st_size > 2_000_000:
        raise ImageRejected("analysis_protocol_error")
    result = json.loads(index.read_bytes())
    result["resources"] = {
        "wall_seconds": time.monotonic() - started,
        "observed_peak_rss_bytes": peak,
        "cpu_limit_seconds": cpu_seconds,
        "memory_limit_mb": memory_mb,
    }
    return result
