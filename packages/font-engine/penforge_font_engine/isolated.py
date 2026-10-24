"""Stdlib-only bounded child runner; no queue, DB, storage or ML dependencies."""

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def rss(pid):
    if sys.platform == "linux":
        try:
            return int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf(
                "SC_PAGE_SIZE"
            )
        except (OSError, ValueError, IndexError):
            return 0
    if sys.platform == "darwin":
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        buffer = ctypes.create_string_buffer(96)
        if lib.proc_pidinfo(pid, 4, 0, buffer, 96) >= 16:
            return ctypes.c_uint64.from_buffer(buffer, 8).value
    return 0


def compile_isolated(
    working,
    manifest,
    glyphs,
    format,
    *,
    cancel_check=lambda: None,
    progress=lambda value: None,
    memory_mb=768,
):
    working = Path(working).resolve()
    working.mkdir(mode=0o700, parents=True, exist_ok=True)
    (working / "input.json").write_text(
        json.dumps(dict(manifest=manifest, glyphs=glyphs, format=format), allow_nan=False)
    )
    command = [sys.executable, "-I", str(Path(__file__).with_name("_child.py"))]
    if sys.platform == "darwin":
        command = [
            "/usr/bin/sandbox-exec",
            "-p",
            "(version 1)(allow default)(deny network*)",
            *command,
        ]
    elif sys.platform != "linux":
        raise ValueError("isolation_unavailable")
    started = time.monotonic()
    last = None
    peak = 0
    with (working / "stdout").open("w+b") as out, (working / "stderr").open("w+b") as err:
        process = subprocess.Popen(
            command,
            cwd=working,
            stdout=out,
            stderr=err,
            start_new_session=True,
            close_fds=True,
            env={
                "PATH": os.defpath,
                "LANG": "C",
                "TMPDIR": str(working),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
        )
        try:
            while process.poll() is None:
                cancel_check()
                peak = max(peak, rss(process.pid))
                if time.monotonic() - started > 60 or peak > memory_mb * 1024 * 1024:
                    raise ValueError("font_resource_limit")
                p = working / "progress.json"
                if p.exists():
                    value = p.read_bytes()
                    if value != last:
                        progress(json.loads(value))
                        last = value
                time.sleep(0.02)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        out.seek(0)
        if process.returncode or out.read(1025) != b'{"ok":true}\n':
            raise ValueError("font_child_failed")
    report_path = working / "report.json"
    font_path = working / "font"
    if (
        report_path.is_symlink()
        or font_path.is_symlink()
        or report_path.stat().st_size > 512 * 1024
        or font_path.stat().st_size > 2 * 1024 * 1024
    ):
        raise ValueError("font_protocol_error")
    report = json.loads(report_path.read_bytes())
    report["resources"]["observed_parent_peak_rss_bytes"] = peak
    return font_path.read_bytes(), report
