"""Bounded PNG container and complete zlib/scanline validation, child-only."""

import struct
import zlib
from collections.abc import Iterator


class PNGInvalid(ValueError):
    pass


def _rows(width: int, height: int, bits_per_pixel: int, interlaced: int) -> Iterator[int]:
    passes = [(0, 0, 1, 1)]
    if interlaced:
        passes = [
            (0, 0, 8, 8),
            (4, 0, 8, 8),
            (0, 4, 4, 8),
            (2, 0, 4, 4),
            (0, 2, 2, 4),
            (1, 0, 2, 2),
            (0, 1, 1, 2),
        ]
    for left, top, step_x, step_y in passes:
        columns = max(0, (width - left + step_x - 1) // step_x)
        rows = max(0, (height - top + step_y - 1) // step_y)
        if columns:
            row_bytes = (columns * bits_per_pixel + 7) // 8
            for _ in range(rows):
                yield row_bytes


class _Scanlines:
    def __init__(self, row_lengths: Iterator[int]):
        self.rows = row_lengths
        self.remaining = 0
        self.decompressor = zlib.decompressobj()

    def feed(self, compressed: bytes) -> None:
        while compressed:
            if self.decompressor.eof:
                raise PNGInvalid("invalid_image_container")
            raw = self.decompressor.decompress(compressed, 65536)
            if self.decompressor.unused_data:
                raise PNGInvalid("invalid_image_container")
            offset = 0
            while offset < len(raw):
                if self.remaining == 0:
                    size = next(self.rows, None)
                    if size is None or raw[offset] > 4:
                        raise PNGInvalid("invalid_image_container")
                    self.remaining = size
                    offset += 1  # the row's PNG filter byte
                count = min(self.remaining, len(raw) - offset)
                offset += count
                self.remaining -= count
            compressed = self.decompressor.unconsumed_tail

    def finish(self) -> None:
        if (
            not self.decompressor.eof
            or self.decompressor.unused_data
            or self.decompressor.unconsumed_tail
            or self.remaining
            or next(self.rows, None) is not None
        ):
            raise PNGInvalid("invalid_image_container")


def verify_png_stream(data: bytes, *, max_dimension: int, max_pixels: int) -> None:
    offset = 8
    stream = None
    seen_data = False
    ended_data = False
    seen_palette = False
    color = -1
    depth = 0
    try:
        while offset + 12 <= len(data):
            length = int.from_bytes(data[offset : offset + 4], "big")
            tag = data[offset + 4 : offset + 8]
            end = offset + 12 + length
            if (
                end > len(data)
                or not all(65 <= char <= 90 or 97 <= char <= 122 for char in tag)
                or tag[2] & 32  # reserved chunk bit must be zero
            ):
                raise PNGInvalid("invalid_image_container")
            payload = data[offset + 8 : end - 4]
            checksum = zlib.crc32(payload, zlib.crc32(tag)) & 0xFFFFFFFF
            if checksum != int.from_bytes(data[end - 4 : end], "big"):
                raise PNGInvalid("invalid_image_container")
            if stream is None:
                if tag != b"IHDR" or length != 13:
                    raise PNGInvalid("invalid_image_container")
                width, height, depth, color, compression, filtering, interlaced = struct.unpack(
                    "!IIBBBBB", payload
                )
                depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
                if (
                    depth not in depths.get(color, set())
                    or compression
                    or filtering
                    or interlaced not in {0, 1}
                ):
                    raise PNGInvalid("invalid_image_container")
                if not width or not height or max(width, height) > max_dimension:
                    raise PNGInvalid("image_dimensions_exceeded")
                if width * height > max_pixels:
                    raise PNGInvalid("image_pixels_exceeded")
                channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
                stream = _Scanlines(_rows(width, height, channels * depth, interlaced))
            elif tag == b"IHDR":
                raise PNGInvalid("invalid_image_container")
            if tag in {b"acTL", b"fcTL", b"fdAT"}:
                raise PNGInvalid("multiple_frames")
            if tag == b"PLTE":
                if (
                    seen_palette
                    or seen_data
                    or color in {0, 4}
                    or length == 0
                    or length % 3
                    or length > 768
                    or (color == 3 and length // 3 > 2**depth)
                ):
                    raise PNGInvalid("invalid_image_container")
                seen_palette = True
            elif tag == b"IDAT":
                if ended_data or (color == 3 and not seen_palette):
                    raise PNGInvalid("invalid_image_container")
                seen_data = True
                stream.feed(payload)
            elif seen_data:
                ended_data = True
            if tag == b"IEND":
                if length or end != len(data) or not seen_data:
                    raise PNGInvalid("invalid_image_container")
                stream.finish()
                return
            if not tag[0] & 32 and tag not in {b"IHDR", b"IDAT", b"PLTE", b"IEND"}:
                raise PNGInvalid("invalid_image_container")
            offset = end
    except zlib.error:
        raise PNGInvalid("invalid_image_container") from None
    raise PNGInvalid("invalid_image_container")
