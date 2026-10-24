"""Bounded subprocess image verification, with immutable source preservation."""

import ctypes
import hashlib
import json
import math
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


class ImageRejected(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DecoderUnavailable(Exception):
    def __init__(self, code: str = "isolation_unavailable"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DecodeLimits:
    # Provisional configurable safety caps, not recognition/quality thresholds.
    max_bytes: int = 20 * 1024 * 1024
    max_pixels: int = 50_000_000
    max_dimension: int = 12000
    derivative_max_dimension: int = 1000
    max_derived_bytes: int = 4 * 1024 * 1024
    memory_mb: int = 768
    cpu_seconds: int = 10
    wall_seconds: float = 20.0

    def __post_init__(self) -> None:
        for key, value in asdict(self).items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
                or (key != "wall_seconds" and not isinstance(value, int))
            ):
                raise ValueError(f"{key} must be positive")


@dataclass(frozen=True)
class VerifiedImage:
    original_sha256: str
    size_bytes: int
    width_px: int
    height_px: int
    pixels: int
    format: str
    derived_path: Path
    derived_sha256: str
    derived_size_bytes: int
    derived_width_px: int
    derived_height_px: int
    metrics: dict[str, float | int | str]


def _rss_bytes(pid: int) -> int:
    if sys.platform == "linux":
        try:
            fields = Path(f"/proc/{pid}/statm").read_text().split()
            return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            return 0
    if sys.platform == "darwin":
        # proc_taskinfo starts with virtual_size then resident_size (uint64).
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        buffer = ctypes.create_string_buffer(96)
        read = library.proc_pidinfo(pid, 4, 0, buffer, len(buffer))
        if read >= 16:
            return ctypes.c_uint64.from_buffer(buffer, 8).value
    return 0


def _command() -> list[str]:
    child = str(Path(__file__).with_name("_decode_child.py").resolve())
    command = [sys.executable, "-I", child]
    if sys.platform == "darwin":
        if not Path("/usr/bin/sandbox-exec").is_file():
            raise DecoderUnavailable()
        return [
            "/usr/bin/sandbox-exec",
            "-p",
            "(version 1)(allow default)(deny network*)",
            *command,
        ]
    if sys.platform == "linux":
        return command
    raise DecoderUnavailable()


def _kill(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def verify_image(
    source_path: Path,
    *,
    declared_mime: str,
    output_dir: Path,
    expected_sha256: str | None = None,
    limits: DecodeLimits | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> VerifiedImage:
    limits = limits or DecodeLimits()
    if declared_mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise ImageRejected("unsupported_image_magic")
    if _rss_bytes(os.getpid()) <= 0:
        raise DecoderUnavailable("memory_monitor_unavailable")
    started = time.monotonic()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Every invocation owns a private directory. Copies pin the same bytes that
    # were hashed, so a source-path replacement cannot race the decoder.
    # Nest all decoder files in the worker's per-attempt directory. If the worker
    # is killed, its lease-aware reaper can inventory and remove the whole tree.
    with tempfile.TemporaryDirectory(prefix="penforge-decode-", dir=output_dir) as temporary:
        working = Path(temporary)
        source = working / "original"
        digest = hashlib.sha256()
        count = 0
        source_fd = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(source_fd, "rb") as stream, source.open("xb") as destination:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ImageRejected("invalid_image_source")
            while chunk := stream.read(65536):
                count += len(chunk)
                if count > limits.max_bytes:
                    raise ImageRejected("image_size_exceeded")
                digest.update(chunk)
                destination.write(chunk)
        actual = digest.hexdigest()
        if expected_sha256 is not None and actual != expected_sha256:
            raise ImageRejected("image_hash_mismatch")
        config = {
            "source": str(source),
            "derivative": str(working / "thumbnail.png"),
            "declared_mime": declared_mime,
            "limits": asdict(limits),
        }
        with (working / "stdout").open("w+b") as stdout, (working / "stderr").open("w+b") as stderr:
            process = subprocess.Popen(
                _command(),
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                cwd=working,
                close_fds=True,
                start_new_session=True,
                env={"PATH": os.defpath, "LANG": "C", "TMPDIR": str(working)},
            )
            assert process.stdin is not None
            process.stdin.write(json.dumps(config).encode())
            process.stdin.close()
            peak_rss = 0
            try:
                while process.poll() is None:
                    if cancel_check is not None:
                        cancel_check()
                    if time.monotonic() - started > limits.wall_seconds:
                        raise ImageRejected("decoder_wall_limit")
                    resident = _rss_bytes(process.pid)
                    peak_rss = max(peak_rss, resident)
                    if resident > limits.memory_mb * 1024 * 1024:
                        raise ImageRejected("decoder_resource_limit")
                    time.sleep(0.02)
            finally:
                if process.poll() is None:
                    _kill(process)
            stdout.seek(0)
            raw = stdout.read(16385)
            if len(raw) > 16384:
                raise DecoderUnavailable("decoder_protocol_error")
            try:
                response = json.loads(raw)
            except (ValueError, TypeError):
                if process.returncode == 71:  # sandbox_apply failed
                    raise DecoderUnavailable() from None
                if process.returncode and process.returncode < 0:
                    raise ImageRejected("decoder_resource_limit") from None
                raise DecoderUnavailable("decoder_protocol_error") from None
        if not response.get("ok"):
            code = response.get("code", "invalid_image_decode")
            if code in {"isolation_unavailable", "strict_decoder_unavailable"}:
                raise DecoderUnavailable(code)
            raise ImageRejected(code)
        result = response["result"]
        result["metrics"]["wall_seconds"] = time.monotonic() - started
        result["metrics"]["observed_peak_rss_bytes"] = peak_rss
        derivative = working / "thumbnail.png"
        if not derivative.is_file() or derivative.stat().st_size > limits.max_derived_bytes:
            raise ImageRejected("derived_size_exceeded")
        with derivative.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != result["derived_sha256"]:
                raise ImageRejected("derived_hash_mismatch")
        published = output_dir / f"thumbnail-{os.urandom(16).hex()}.png"
        os.replace(derivative, published)
        return VerifiedImage(actual, count, derived_path=published, **result)
