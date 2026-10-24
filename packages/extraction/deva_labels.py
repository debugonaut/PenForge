"""Shaped reference labels only. Never used to synthesize user handwriting."""

import hashlib
import io
from pathlib import Path

import freetype
import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from PIL import Image

FONT = (
    Path(__file__).resolve().parents[2] / "research/fixtures/fonts/noto-deva/NotoSansDevanagari.ttf"
)


def reference(provenance):
    data = FONT.read_bytes()
    if (
        not provenance
        or provenance["reference_font_sha256"] != hashlib.sha256(data).hexdigest()
        or provenance["harfbuzz"] != hb.version_string()
    ):
        raise ValueError("label_reference_mismatch")
    font = hb.Font(hb.Face(data))
    font.scale = (font.face.upem, font.face.upem)
    return data, font


def shaped(cps, provenance):
    data, font = reference(provenance)
    buf = hb.Buffer()
    buf.add_codepoints(cps)
    buf.script = "Deva"
    buf.direction = "ltr"
    buf.language = "hi"
    hb.shape(font, buf)
    if any(i.codepoint == 0 for i in buf.glyph_infos):
        raise ValueError("reference_label_missing")
    return data, font, list(zip(buf.glyph_infos, buf.glyph_positions))


def label_svg(cps, x, y, size, provenance):
    data, font, items = shaped(cps, provenance)
    f = TTFont(io.BytesIO(data))
    gs = f.getGlyphSet()
    order = f.getGlyphOrder()
    values = []
    cursor = 0
    scale = size / font.face.upem
    for info, pos in items:
        pen = SVGPathPen(gs)
        gs[order[info.codepoint]].draw(pen)
        values.append(
            f'<path d="{pen.getCommands()}" transform="translate({x + (cursor + pos.x_offset) * scale} {y - pos.y_offset * scale}) scale({scale} {-scale})"/>'
        )
        cursor += pos.x_advance
    return '<g fill="#536b81">' + "".join(values) + "</g>"


def paint_label(image, cps, xy, size, provenance):
    data, font, items = shaped(cps, provenance)
    face = freetype.Face(io.BytesIO(data))
    face.set_pixel_sizes(0, size)
    cursor = 0
    scale = size / font.face.upem
    for info, pos in items:
        face.load_glyph(info.codepoint, freetype.FT_LOAD_RENDER)
        g = face.glyph
        b = g.bitmap
        if b.width and b.rows:
            raw = bytes(b.buffer)
            mask = Image.frombytes("L", (b.width, b.rows), raw, "raw", "L", abs(b.pitch))
            image.paste(
                "#536b81",
                (
                    round(xy[0] + (cursor + pos.x_offset) * scale) + g.bitmap_left,
                    round(xy[1] + size - pos.y_offset * scale) - g.bitmap_top,
                ),
                mask,
            )
        cursor += pos.x_advance
