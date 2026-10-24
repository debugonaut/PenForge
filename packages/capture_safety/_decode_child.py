"""Private subprocess entry point. No user-controlled executable or arguments."""

import hashlib
import json
import resource
import signal
import struct
import sys
import time
import warnings
from pathlib import Path

# -I removes script-directory imports. This fixed source directory is trusted;
# no capture filename or image metadata controls the module search path.
sys.path.insert(0, str(Path(__file__).parent))
from _jpeg_strict import verify_jpeg_entropy  # type: ignore[import-not-found] # noqa: E402
from _png_strict import PNGInvalid, verify_png_stream  # type: ignore[import-not-found] # noqa: E402
from _sandbox import install_and_check_network_deny  # type: ignore[import-not-found] # noqa: E402


class Rejected(Exception):
    pass


def check_jpeg(data: bytes) -> None:
    offset = 2
    saw_scan = False
    while offset < len(data):
        if data[offset] != 0xFF:
            raise Rejected("invalid_image_container")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            if offset != len(data) or not saw_scan:
                raise Rejected("invalid_image_container")
            return
        if marker in {0, 0xD8} or 0xD0 <= marker <= 0xD7:
            raise Rejected("invalid_image_container")
        if marker == 1:  # standalone TEM marker
            continue
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            break
        offset += length
        if marker == 0xDA:
            saw_scan = True
            # Entropy bytes may contain stuffed FF00 or restart markers. Other
            # markers resume the outer parser (including progressive JPEG scans).
            while offset < len(data):
                marker_at = data.find(b"\xff", offset)
                if marker_at < 0:
                    raise Rejected("invalid_image_container")
                next_byte = marker_at + 1
                while next_byte < len(data) and data[next_byte] == 0xFF:
                    next_byte += 1
                if next_byte >= len(data):
                    raise Rejected("invalid_image_container")
                if data[next_byte] == 0 or 0xD0 <= data[next_byte] <= 0xD7:
                    offset = next_byte + 1
                    continue
                offset = marker_at
                break
    raise Rejected("invalid_image_container")


def check_webp(data: bytes) -> None:
    if len(data) < 20 or int.from_bytes(data[4:8], "little") + 8 != len(data):
        raise Rejected("invalid_image_container")
    offset = 12
    images = 0
    seen: set[bytes] = set()
    allowed = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ICCP", b"EXIF", b"XMP "}
    while offset + 8 <= len(data):
        tag = data[offset : offset + 4]
        length = int.from_bytes(data[offset + 4 : offset + 8], "little")
        end = offset + 8 + length
        if tag in {b"ANIM", b"ANMF"}:
            raise Rejected("multiple_frames")
        if tag not in allowed or tag in seen or end + (length % 2) > len(data):
            raise Rejected("invalid_image_container")
        seen.add(tag)
        if tag == b"VP8X" and (length != 10 or data[offset + 8] & 2):
            raise Rejected("multiple_frames")
        if tag in {b"VP8 ", b"VP8L"}:
            images += 1
        if length % 2 and data[end] != 0:
            raise Rejected("invalid_image_container")
        offset = end + length % 2
    if offset != len(data) or images != 1:
        raise Rejected("invalid_image_container")


def decode(config: dict) -> dict:
    limits = config["limits"]
    # The child owns a wall alarm too: the parent's watchdog cannot run after
    # kill9. A dormant native decoder cannot then survive indefinitely.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, limits["wall_seconds"])
    memory = limits["memory_mb"] * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"] + 1))
    # Darwin exposes AS/DATA but rejects finite values on this runtime. The
    # parent's native resident-memory watchdog is required there; Linux enforces
    # AS/DATA in addition. This platform distinction is recorded in metrics.
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_DATA, (memory, memory))
    # The bounded JSON protocol also uses a regular file; allow its small envelope
    # even when a deliberately tiny derivative cap is tested.
    file_limit = max(16384, limits["max_derived_bytes"])
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    isolation = install_and_check_network_deny()

    # Pillow and its native parsers are worker-only dependencies; no import or
    # image inspection occurs before OS isolation and resource limits are active.
    from PIL import Image, ImageFile, ImageOps, PngImagePlugin, __version__

    Image.MAX_IMAGE_PIXELS = limits["max_pixels"]
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    PngImagePlugin.MAX_TEXT_CHUNK = 65536
    PngImagePlugin.MAX_TEXT_MEMORY = 1024 * 1024
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    path = Path(config["source"])
    data = path.read_bytes()
    if data.startswith(b"\xff\xd8\xff"):
        mime, image_format = "image/jpeg", "JPEG"
        check_jpeg(data)
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime, image_format = "image/png", "PNG"
        try:
            verify_png_stream(
                data, max_dimension=limits["max_dimension"], max_pixels=limits["max_pixels"]
            )
        except PNGInvalid as error:
            raise Rejected(str(error)) from None
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime, image_format = "image/webp", "WEBP"
        check_webp(data)
    else:
        raise Rejected("unsupported_image_magic")
    del data
    if mime != config["declared_mime"]:
        raise Rejected("image_mime_mismatch")
    try:
        with Image.open(path, formats=[image_format]) as image:
            width, height = image.size
            if max(width, height) > limits["max_dimension"] or min(width, height) < 1:
                raise Rejected("image_dimensions_exceeded")
            if width * height > limits["max_pixels"]:
                raise Rejected("image_pixels_exceeded")
            if getattr(image, "n_frames", 1) != 1:
                raise Rejected("multiple_frames")
            image.verify()
        if image_format == "JPEG":
            # Pillow's native JPEG adapter intentionally suppresses recoverable
            # corrupt-data warnings, including absent/truncated entropy with EOI.
            # Validate the full entropy stream with those warnings made fatal.
            verify_jpeg_entropy(path.read_bytes())
        with Image.open(path, formats=[image_format]) as image:
            image.load()  # verify() is structural and does not decode pixels.
            if image.format != image_format:
                raise Rejected("image_mime_mismatch")
            ImageOps.exif_transpose(image, in_place=True)
            image.thumbnail((limits["derivative_max_dimension"],) * 2)
            mode = "RGBA" if image.mode in {"RGBA", "LA"} or "transparency" in image.info else "RGB"
            clean = Image.new(mode, image.size)
            clean.paste(image.convert(mode))
            clean.save(config["derivative"], format="PNG", optimize=False)
            derivative_width, derivative_height = clean.size
            clean.close()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise Rejected("image_pixels_exceeded") from None
    derivative = Path(config["derivative"])
    size = derivative.stat().st_size
    if size > limits["max_derived_bytes"]:
        raise Rejected("derived_size_exceeded")
    with derivative.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss_bytes = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
    return {
        "width_px": width,
        "height_px": height,
        "pixels": width * height,
        "format": image_format,
        "derived_size_bytes": size,
        "derived_sha256": digest,
        "derived_width_px": derivative_width,
        "derived_height_px": derivative_height,
        "metrics": {
            "cpu_seconds": usage.ru_utime + usage.ru_stime,
            "peak_rss_bytes": rss_bytes,
            "isolation": isolation,
            "memory_enforcement": "rlimit_as_and_rss"
            if sys.platform == "linux"
            else "rss_watchdog",
            "pillow_version": __version__,
        },
    }


if __name__ == "__main__":
    started = time.monotonic()
    try:
        config = json.loads(sys.stdin.buffer.read(8192))
        result = decode(config)
        result["metrics"]["decode_wall_seconds"] = time.monotonic() - started
        print(json.dumps({"ok": True, "result": result}, separators=(",", ":")))
    except Rejected as error:
        print(json.dumps({"ok": False, "code": str(error)}))
    except RuntimeError as error:
        code = str(error)
        if code not in {"isolation_unavailable", "strict_decoder_unavailable"}:
            code = "isolation_unavailable"
        print(json.dumps({"ok": False, "code": code}))
    except MemoryError:
        print(json.dumps({"ok": False, "code": "decoder_resource_limit"}))
    except (OSError, ValueError, SyntaxError, struct.error, OverflowError, EOFError):
        print(json.dumps({"ok": False, "code": "invalid_image_decode"}))
