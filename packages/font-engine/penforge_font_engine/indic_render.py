"""FreeType paints HarfBuzz glyph positions, preserving Indic shaping."""

import hashlib
import io
import json

import freetype
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw

from .compiler import sfnt_bytes
from .shaping import STARTER, shape_run, supported_input


def paint(image, data, cps, *, xy=(30, 120), size=64, features=None):
    run = shape_run(data, cps, features)
    raw = sfnt_bytes(data)
    f = TTFont(io.BytesIO(raw))
    scale = size / f["head"].unitsPerEm
    face = freetype.Face(io.BytesIO(raw))
    face.set_pixel_sizes(0, size)
    cursor = 0
    for gid, pos in zip(run["glyph_ids"], run["positions"]):
        face.load_glyph(gid, freetype.FT_LOAD_RENDER)
        g = face.glyph
        b = g.bitmap
        if b.width and b.rows:
            mask = Image.frombytes(
                "L", (b.width, b.rows), bytes(b.buffer), "raw", "L", abs(b.pitch)
            )
            image.paste(
                "black",
                (
                    round(xy[0] + (cursor + pos["x_offset"]) * scale) + g.bitmap_left,
                    round(xy[1] - pos["y_offset"] * scale) - g.bitmap_top,
                ),
                mask,
            )
        cursor += pos["x_advance"]
    return run


def render_smoke(data, working):
    f = TTFont(io.BytesIO(sfnt_bytes(data)))
    cmap = f.getBestCmap()
    cases = json.loads(STARTER.read_bytes())["cases"]
    supported = [c for c in cases if supported_input(c["codepoints"], cmap)]
    if not supported:
        supported = [dict(case_id="partial", codepoints=[cp]) for cp in cmap if cp != 32][:16]
    image = Image.new("RGB", (1000, max(220, len(supported) * 155 + 80)), "white")
    draw = ImageDraw.Draw(image)
    for i, c in enumerate(supported):
        draw.text((20, 10 + i * 155), c["case_id"], fill="#666666")
        paint(image, data, c["codepoints"], xy=(60, 120 + i * 155))
    if min(image.convert("L").getdata()) >= 250:
        raise ValueError("blank_indic_render")
    image.save(working / "render.png")
    return dict(
        status="passed",
        renderer="FreeType glyph raster + HarfBuzz positions",
        freetype_version=".".join(map(str, freetype.version())),
        rendered_cases=len(supported),
        render_sha256=hashlib.sha256((working / "render.png").read_bytes()).hexdigest(),
        linguistic_approval=False,
    )
