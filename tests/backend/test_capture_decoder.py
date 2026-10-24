import hashlib
import os
import subprocess
import sys
import zlib
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin
from PIL.TiffImagePlugin import IFDRational

from packages.capture_safety import (
    DecodeLimits,
    DecoderUnavailable,
    ImageRejected,
    verify_image,
)
from packages.capture_safety.decoder import _command


def fixture(tmp_path: Path, image_format: str = "PNG", size=(96, 64), **options) -> Path:
    path = tmp_path / f"source.{image_format.lower()}"
    with Image.new("RGB", size, "#f1e8ce") as image:
        image.save(path, format=image_format, **options)
    return path


@pytest.mark.parametrize(
    "image_format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")]
)
def test_safe_full_decode_hash_and_metadata_free_bounded_derivative(tmp_path, image_format, mime):
    source = fixture(tmp_path, image_format, size=(1300, 700))
    original = source.read_bytes()
    result = verify_image(
        source,
        declared_mime=mime,
        expected_sha256=hashlib.sha256(original).hexdigest(),
        output_dir=tmp_path / "out",
    )
    assert source.read_bytes() == original
    assert result.original_sha256 == hashlib.sha256(original).hexdigest()
    assert result.width_px == 1300 and result.height_px == 700 and result.pixels == 910000
    assert result.derived_size_bytes <= DecodeLimits().max_derived_bytes
    assert max(result.derived_width_px, result.derived_height_px) <= 1000
    assert result.metrics["cpu_seconds"] > 0 and result.metrics["peak_rss_bytes"] > 0
    assert result.metrics["observed_peak_rss_bytes"] > 0
    assert "network_deny" in result.metrics["isolation"]
    with Image.open(result.derived_path) as derivative:
        derivative.load()
        assert derivative.format == "PNG"
        assert derivative.info == {} and len(derivative.getexif()) == 0


def test_exif_orientation_is_applied_only_to_derivative_and_identifying_metadata_removed(tmp_path):
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private identifying description"
    exif[315] = "private identifying author"
    exif[34853] = {
        1: "N",
        2: (IFDRational(12), IFDRational(34), IFDRational(56)),
        3: "E",
        4: (IFDRational(78), IFDRational(9), IFDRational(10)),
    }
    source = fixture(tmp_path, "JPEG", size=(80, 40), exif=exif)
    original = source.read_bytes()
    result = verify_image(source, declared_mime="image/jpeg", output_dir=tmp_path / "out")
    assert source.read_bytes() == original
    with Image.open(source) as original_image:
        assert original_image.getexif().get_ifd(34853)[1] == "N"
    assert result.width_px == 80 and result.height_px == 40
    with Image.open(result.derived_path) as derivative:
        assert derivative.size == (40, 80)
        assert derivative.info == {} and len(derivative.getexif()) == 0
    assert b"private identifying" not in result.derived_path.read_bytes()


def test_png_text_and_metadata_stripped_without_modifying_original(tmp_path):
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Author", "private author")
    metadata.add_text("XML:com.adobe.xmp", "private XMP")
    source = fixture(tmp_path, pnginfo=metadata)
    result = verify_image(source, declared_mime="image/png", output_dir=tmp_path / "out")
    assert b"private author" in source.read_bytes()
    with Image.open(result.derived_path) as derivative:
        assert derivative.info == {}


@pytest.mark.parametrize(
    "image_format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")]
)
def test_truncated_and_trailing_polyglot_files_rejected(tmp_path, image_format, mime):
    source = fixture(tmp_path, image_format)
    data = source.read_bytes()
    for malformed in [data[:-2], data + b"PK\x03\x04zip appended", data + data]:
        source.write_bytes(malformed)
        with pytest.raises(ImageRejected):
            verify_image(source, declared_mime=mime, output_dir=tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_wrong_size_hash_mime_magic_crc_dimensions_and_pixels_rejected(tmp_path):
    source = fixture(tmp_path)
    original = source.read_bytes()
    with pytest.raises(ImageRejected, match="image_hash_mismatch"):
        verify_image(
            source, declared_mime="image/png", expected_sha256="0" * 64, output_dir=tmp_path / "out"
        )
    with pytest.raises(ImageRejected, match="image_size_exceeded"):
        verify_image(
            source,
            declared_mime="image/png",
            output_dir=tmp_path / "out",
            limits=replace(DecodeLimits(), max_bytes=3),
        )
    with pytest.raises(ImageRejected, match="image_mime_mismatch"):
        verify_image(source, declared_mime="image/jpeg", output_dir=tmp_path / "out")
    with pytest.raises(ImageRejected, match="image_dimensions_exceeded"):
        verify_image(
            source,
            declared_mime="image/png",
            output_dir=tmp_path / "out",
            limits=replace(DecodeLimits(), max_dimension=10),
        )
    with pytest.raises(ImageRejected, match="image_pixels_exceeded"):
        verify_image(
            source,
            declared_mime="image/png",
            output_dir=tmp_path / "out",
            limits=replace(DecodeLimits(), max_pixels=10),
        )
    source.write_bytes(original[:32] + bytes([original[32] ^ 1]) + original[33:])
    with pytest.raises(ImageRejected, match="invalid_image_container"):
        verify_image(source, declared_mime="image/png", output_dir=tmp_path / "out")
    source.write_bytes(b"%PDF unsupported")
    with pytest.raises(ImageRejected, match="unsupported_image_magic"):
        verify_image(source, declared_mime="image/png", output_dir=tmp_path / "out")


@pytest.mark.parametrize("image_format,mime", [("PNG", "image/png"), ("WEBP", "image/webp")])
def test_animated_images_are_rejected(tmp_path, image_format, mime):
    source = tmp_path / "animated"
    with Image.new("RGB", (16, 16), "white") as one, Image.new("RGB", (16, 16), "black") as two:
        one.save(
            source, format=image_format, save_all=True, append_images=[two], duration=100, loop=0
        )
    with pytest.raises(ImageRejected, match="multiple_frames"):
        verify_image(source, declared_mime=mime, output_dir=tmp_path / "out")


def test_progressive_jpeg_is_valid_and_corrupt_full_decode_fails(tmp_path):
    source = fixture(tmp_path, "JPEG", progressive=True)
    verify_image(source, declared_mime="image/jpeg", output_dir=tmp_path / "out")
    data = source.read_bytes()
    source.write_bytes(data[: data.find(b"\xff\xda") + 8] + b"\xff\xd9")
    with pytest.raises(ImageRejected):
        verify_image(source, declared_mime="image/jpeg", output_dir=tmp_path / "out")


@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("fraction", [0, 0.5])
def test_truncated_jpeg_entropy_with_valid_eoi_is_rejected(tmp_path, progressive, fraction):
    # A valid SOS header and EOI do not prove the image contains all MCUs. Pillow
    # alone accepts both of these and silently fills missing scan data.
    source = fixture(tmp_path, "JPEG", size=(256, 256), progressive=progressive)
    original = source.read_bytes()
    scan = original.index(b"\xff\xda")
    entropy = scan + 2 + int.from_bytes(original[scan + 2 : scan + 4], "big")
    following = original.find(b"\xff", entropy)
    if following < 0:
        following = len(original) - 2
    end = entropy + int((following - entropy) * fraction)
    source.write_bytes(original[:end] + b"\xff\xd9")
    with pytest.raises(ImageRejected, match="invalid_image_decode"):
        verify_image(source, declared_mime="image/jpeg", output_dir=tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        len(payload).to_bytes(4, "big")
        + tag
        + payload
        + (zlib.crc32(tag + payload) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def replace_png_image_data(data: bytes, transform) -> bytes:
    offset = 8
    result = data[:8]
    while offset < len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        tag = data[offset + 4 : offset + 8]
        if tag == b"IDAT":
            result += transform(data[offset + 8 : offset + 8 + length])
        else:
            result += data[offset : offset + length + 12]
        offset += length + 12
    return result


@pytest.mark.parametrize(
    "mutation",
    [
        "trailing",
        "second_stream",
        "empty_scanlines",
        "short_scanlines",
        "extra_scanlines",
        "bad_filter",
        "unfinished_zlib",
    ],
)
def test_png_idat_stream_must_end_exactly_at_complete_scanlines(tmp_path, mutation):
    source = fixture(tmp_path)

    def malformed(payload):
        raw = zlib.decompress(payload)
        if mutation == "trailing":
            payload += b"PK\x03\x04internal trailing payload"
        elif mutation == "second_stream":
            payload += zlib.compress(b"another stream")
        elif mutation == "empty_scanlines":
            payload = zlib.compress(b"")
        elif mutation == "short_scanlines":
            payload = zlib.compress(raw[:-1])
        elif mutation == "extra_scanlines":
            payload = zlib.compress(raw + b"\x00extra")
        elif mutation == "bad_filter":
            payload = zlib.compress(b"\x05" + raw[1:])
        else:
            payload = payload[:-1]
        # Valid PNG chunk CRC and IEND isolate compressed-stream validation.
        return png_chunk(b"IDAT", payload)

    source.write_bytes(replace_png_image_data(source.read_bytes(), malformed))
    with pytest.raises(ImageRejected, match="invalid_image_container"):
        verify_image(source, declared_mime="image/png", output_dir=tmp_path / "out")
    assert list((tmp_path / "out").iterdir()) == []


def test_png_valid_multiple_idat_chunks_and_adam7_scanlines_are_preserved(tmp_path):
    source = fixture(tmp_path)

    def split(payload):
        return b"".join(
            png_chunk(b"IDAT", payload[index : index + 3]) for index in range(0, len(payload), 3)
        )

    source.write_bytes(replace_png_image_data(source.read_bytes(), split))
    verify_image(source, declared_mime="image/png", output_dir=tmp_path / "out")
    width, height = 17, 19
    passes = [
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    ]
    raw = b""
    for left, top, step_x, step_y in passes:
        for row in range(top, height, step_y):
            values = bytes((column * 7 + row * 11) % 256 for column in range(left, width, step_x))
            if values:
                raw += b"\x00" + values
    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 0, 0, 0, 1])
    source.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(raw))
        + png_chunk(b"IEND", b"")
    )
    result = verify_image(source, declared_mime="image/png", output_dir=tmp_path / "out")
    assert (result.width_px, result.height_px) == (width, height)
    with Image.open(result.derived_path) as decoded:
        assert decoded.getpixel((4, 5)) == ((4 * 7 + 5 * 11) % 256,) * 3


def test_strict_jpeg_unsupported_abi_fails_closed(monkeypatch):
    from PIL import features

    from packages.capture_safety._jpeg_strict import verify_jpeg_entropy

    monkeypatch.setattr(features, "version_codec", lambda _name: "unsupported")
    with pytest.raises(RuntimeError, match="strict_decoder_unavailable"):
        verify_jpeg_entropy(b"not parsed without supported ABI")


def test_wall_memory_derivative_limits_and_cancellation_clean_temporary_files(
    tmp_path, monkeypatch
):
    source = fixture(tmp_path)
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    for changed, expected in [
        ({"wall_seconds": 0.000001}, "decoder_wall_limit"),
        ({"memory_mb": 1}, "decoder_resource_limit"),
        ({"max_derived_bytes": 40}, None),
    ]:
        with pytest.raises(ImageRejected, match=expected):
            verify_image(
                source,
                declared_mime="image/png",
                output_dir=tmp_path / "out",
                limits=replace(DecodeLimits(), **changed),
            )
        assert not list(tmp_path.rglob("penforge-decode-*"))

    def lost_lease():
        raise RuntimeError("lease_lost")

    with pytest.raises(RuntimeError, match="lease_lost"):
        verify_image(
            source, declared_mime="image/png", output_dir=tmp_path / "out", cancel_check=lost_lease
        )
    assert not list(tmp_path.rglob("penforge-decode-*"))
    assert list((tmp_path / "out").iterdir()) == []


def test_isolation_failure_is_closed_and_network_syscalls_are_actually_denied(
    tmp_path, monkeypatch
):
    source = fixture(tmp_path)
    monkeypatch.setattr(
        "packages.capture_safety.decoder._command",
        lambda: [sys.executable, "-c", "raise SystemExit(71)"],
    )
    with pytest.raises(DecoderUnavailable, match="isolation_unavailable"):
        verify_image(source, declared_mime="image/png", output_dir=tmp_path / "out")
    monkeypatch.undo()
    # Use the same process/network sandbox launcher and install the Linux native
    # filter. The probe itself checks real IPv4+IPv6 connect calls, not mocks.
    command = _command()
    code = (
        f"import sys; sys.path.insert(0,{str(Path('packages/capture_safety').resolve())!r}); "
        "from _sandbox import install_and_check_network_deny; "
        "print(install_and_check_network_deny())"
    )
    process = subprocess.run(
        command[:-1] + ["-c", code], capture_output=True, timeout=5, env={"PATH": os.defpath}
    )
    assert process.returncode == 0
    assert b"network_deny" in process.stdout


def test_resource_configuration_rejects_nonpositive_values():
    with pytest.raises(ValueError):
        DecodeLimits(max_pixels=0)
