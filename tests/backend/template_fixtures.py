"""Lossless print-raster checks independent of platform PNG compression."""

import hashlib
import io

from PIL import Image


def assert_png_raster_equal(expected: bytes, actual: bytes) -> None:
    with Image.open(io.BytesIO(expected)) as before, Image.open(io.BytesIO(actual)) as after:
        assert before.format == after.format == "PNG", "PNG format differs"
        assert before.mode == after.mode, "PNG mode differs"
        assert before.size == after.size, "PNG dimensions differ"
        assert before.info == after.info, "PNG metadata differs"
        # Hash every decoded byte without constructing a megabyte-sized pytest
        # diff. Do not resize, convert modes, threshold or tolerate changed pixels.
        expected_hash = hashlib.sha256(before.tobytes()).hexdigest()
        actual_hash = hashlib.sha256(after.tobytes()).hexdigest()
        assert expected_hash == actual_hash, (
            f"PNG pixels differ: expected={expected_hash} actual={actual_hash}"
        )
