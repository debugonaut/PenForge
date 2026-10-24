"""Pinned public templates. Generator and extractor consume the same manifest."""

from __future__ import annotations

import html
import io
from pathlib import Path

import cv2
import numpy as np
from penforge_contracts.schemas import TemplateManifest
from penforge_contracts.serialization import content_hash
from PIL import Image, ImageDraw, ImageFont

TEMPLATE_DIRECTORY = Path(__file__).resolve().parents[1] / "contracts" / "templates"
PIXELS_PER_MM = 8.0


def load_template(page_index: int = 0) -> TemplateManifest:
    if type(page_index) is not int or page_index not in (0, 1):
        raise ValueError("unknown_template_page")
    return TemplateManifest.model_validate_json(
        (TEMPLATE_DIRECTORY / f"penforge-latin-ascii-1.0.0-page-{page_index}.json").read_bytes()
    )


def template_hash(manifest: TemplateManifest) -> str:
    return content_hash(manifest.model_dump(mode="json"))


def marker_image(marker_id: int, size: int) -> np.ndarray:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    return cv2.aruco.generateImageMarker(dictionary, marker_id, size)


def qr_image(payload: str, quiet_zone_modules: int = 4) -> np.ndarray:
    encoded = cv2.QRCodeEncoder_create().encode(payload)  # type: ignore[attr-defined]
    if type(quiet_zone_modules) is not int or not 4 <= quiet_zone_modules <= 16:
        raise ValueError("invalid_qr_quiet_zone")
    black = np.argwhere(encoded < 128)
    top, left = black.min(axis=0)
    bottom, right = black.max(axis=0) + 1
    data = encoded[top:bottom, left:right]
    return cv2.copyMakeBorder(
        data,
        quiet_zone_modules,
        quiet_zone_modules,
        quiet_zone_modules,
        quiet_zone_modules,
        cv2.BORDER_CONSTANT,
        value=255,
    )


def _identity_rect(manifest: TemplateManifest) -> tuple[float, float, float, float]:
    rect = manifest.page_identity.rect_mm
    if rect is None or manifest.page_identity.quiet_zone_modules is None:
        raise ValueError("template_identity_geometry_unpinned")
    return rect.x, rect.y, rect.width, rect.height


def template_png(
    manifest: TemplateManifest, *, pixels_per_mm: float = PIXELS_PER_MM, include_labels: bool = True
) -> bytes:
    if manifest.printing is None or manifest.markers.quiet_zone_mm is None:
        raise ValueError("template_print_geometry_unpinned")
    if not 4 <= pixels_per_mm <= 24:
        raise ValueError("template_raster_scale_out_of_bounds")
    printing = manifest.printing
    scale = pixels_per_mm
    image = Image.new(
        "RGB",
        (round(manifest.page.width_mm * scale), round(manifest.page.height_mm * scale)),
        "white",
    )
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(8, round(2.4 * scale)))

    def rect(x, y, w, h, color, width=1):
        draw.rectangle(
            (round(x * scale), round(y * scale), round((x + w) * scale), round((y + h) * scale)),
            outline=color,
            width=width,
        )

    for marker in manifest.markers.items:
        r = marker.rect_mm
        bitmap = Image.fromarray(marker_image(marker.marker_id, round(r.width * scale)))
        image.paste(bitmap, (round(r.x * scale), round(r.y * scale)))
    x, y, w, h = _identity_rect(manifest)
    qr = Image.fromarray(
        qr_image(
            manifest.page_identity.public_payload, manifest.page_identity.quiet_zone_modules or 4
        )
    ).resize((round(w * scale), round(h * scale)), Image.Resampling.NEAREST)
    image.paste(qr, (round(x * scale), round(y * scale)))
    guides = {g.guide_id: g for g in manifest.guides}
    for cell in manifest.cells:
        r = cell.rect_mm
        guide = guides[cell.guide_id]
        rect(r.x, r.y, r.width, r.height, printing.border_color)
        offsets = [guide.baseline_offset_mm]
        for name in (
            "cap_height_offset_mm",
            "x_height_offset_mm",
            "descender_offset_mm",
            "headline_offset_mm",
            "upper_mark_offset_mm",
            "lower_mark_offset_mm",
        ):
            offset = getattr(guide, name, None)
            if offset is not None:
                offsets.append(offset)
        for offset in offsets:
            draw.line(
                (
                    round((r.x + 0.4) * scale),
                    round((r.y + offset) * scale),
                    round((r.x + r.width - 0.4) * scale),
                    round((r.y + offset) * scale),
                ),
                fill=printing.guide_color,
                width=max(1, round(0.14 * scale)),
            )
        if include_labels:
            if manifest.script_code == "Deva":
                from .deva_labels import paint_label

                paint_label(
                    image,
                    cell.codepoints,
                    (round(r.x * scale), round((r.y - 4.0) * scale)),
                    max(12, round(3.0 * scale)),
                    manifest.label_provenance,
                )
                continue
            label = f"{chr(cell.codepoints[0])}  U+{cell.codepoints[0]:04X}"
            draw.text(
                (round(r.x * scale), round((r.y - 3.3) * scale)), label, font=font, fill="#536b81"
            )
    title = (
        "PENFORGE / DEVANAGARI / ENGINEERING v1"
        if manifest.script_code == "Deva"
        else "PENFORGE / LATIN ASCII / 1.0.0"
    )
    draw.text((round(35 * scale), round(39 * scale)), title, font=font, fill="#303030")
    draw.text(
        (round(35 * scale), round(43 * scale)),
        (
            f"Page {manifest.page.index + 1} - write inside each cell; use dark ink."
            if manifest.script_code == "Deva"
            else f"Page {manifest.page.index + 1}/2 - write inside each cell; use dark ink."
        ),
        font=font,
        fill="#303030",
    )
    draw.text(
        (round(30 * scale), round(261 * scale)),
        "Write the shown character or cluster. Keep all markers and QR visible."
        if manifest.script_code == "Deva"
        else "One symbol per cell. Empty cells are allowed. Keep every marker and QR visible.",
        font=font,
        fill="#303030",
    )
    draw.text(
        (round(30 * scale), round(265 * scale)),
        "Guides: upper marks, headline, baseline, lower marks. Labels are context only."
        if manifest.script_code == "Deva"
        else "Guides: cap height, x-height, baseline, descender. Use black or dark neutral ink.",
        font=font,
        fill="#303030",
    )
    reference = printing.scale_reference_rect_mm
    center_y = reference.y + reference.height / 2
    draw.text(
        (round(30 * scale), round(280 * scale)),
        f"Print at 100% / Actual size. {reference.width:g} mm reference:",
        font=font,
        fill="#303030",
    )
    draw.line(
        (
            round(reference.x * scale),
            round(center_y * scale),
            round((reference.x + reference.width) * scale),
            round(center_y * scale),
        ),
        fill="black",
        width=max(1, round(0.25 * scale)),
    )
    for line_x in (reference.x, reference.x + reference.width):
        draw.line(
            (
                round(line_x * scale),
                round(reference.y * scale),
                round(line_x * scale),
                round((reference.y + reference.height) * scale),
            ),
            fill="black",
            width=max(1, round(0.25 * scale)),
        )
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=6)
    return output.getvalue()


def _bitmap_svg(bitmap: np.ndarray, x: float, y: float, width: float, height: float) -> str:
    rows, cols = bitmap.shape
    values = []
    for row, col in np.argwhere(bitmap < 128):
        values.append(
            f'<rect x="{x + col * width / cols:.6f}" y="{y + row * height / rows:.6f}" width="{width / cols + 0.001:.6f}" height="{height / rows + 0.001:.6f}"/>'
        )
    return '<g fill="black">' + "".join(values) + "</g>"


def template_svg(manifest: TemplateManifest) -> str:
    if manifest.printing is None or manifest.markers.quiet_zone_mm is None:
        raise ValueError("template_print_geometry_unpinned")
    printing = manifest.printing
    reference = printing.scale_reference_rect_mm
    center_y = reference.y + reference.height / 2
    values = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{manifest.page.width_mm}mm" height="{manifest.page.height_mm}mm" viewBox="0 0 {manifest.page.width_mm} {manifest.page.height_mm}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f"<metadata>manifest={template_hash(manifest)}</metadata>",
    ]
    for marker in manifest.markers.items:
        r = marker.rect_mm
        values.append(_bitmap_svg(marker_image(marker.marker_id, 6), r.x, r.y, r.width, r.height))
    values.append(
        _bitmap_svg(
            qr_image(
                manifest.page_identity.public_payload,
                manifest.page_identity.quiet_zone_modules or 4,
            ),
            *_identity_rect(manifest),
        )
    )
    guides = {g.guide_id: g for g in manifest.guides}
    for cell in manifest.cells:
        r = cell.rect_mm
        guide = guides[cell.guide_id]
        values.append(
            f'<rect x="{r.x}" y="{r.y}" width="{r.width}" height="{r.height}" fill="none" stroke="{printing.border_color}" stroke-width=".15"/>'
        )
        for offset in [
            guide.baseline_offset_mm,
            *[
                getattr(guide, k, None)
                for k in (
                    "cap_height_offset_mm",
                    "x_height_offset_mm",
                    "descender_offset_mm",
                    "headline_offset_mm",
                    "upper_mark_offset_mm",
                    "lower_mark_offset_mm",
                )
            ],
        ]:
            if offset is not None:
                values.append(
                    f'<path d="M {r.x + 0.4} {r.y + offset} H {r.x + r.width - 0.4}" fill="none" stroke="{printing.guide_color}" stroke-width=".14"/>'
                )
        if manifest.script_code == "Deva":
            from .deva_labels import label_svg

            values.append(label_svg(cell.codepoints, r.x, r.y - 1, 3.0, manifest.label_provenance))
            continue
        label = html.escape(f"{chr(cell.codepoints[0])} U+{cell.codepoints[0]:04X}")
        values.append(
            f'<text x="{r.x}" y="{r.y - 0.8}" font-family="sans-serif" font-size="2.3" fill="#536b81">{label}</text>'
        )
    values += [
        f'<text x="35" y="41" font-family="sans-serif" font-size="3" fill="#303030">PENFORGE / LATIN ASCII 1.0.0 / Page {manifest.page.index + 1} of 2</text>',
        '<text x="35" y="46" font-family="sans-serif" font-size="2.4">Write inside each cell. Dark ink. Print at 100% / Actual size.</text>',
        '<text x="30" y="263" font-family="sans-serif" font-size="2.4">One symbol per cell. Empty cells are allowed. Keep every marker and QR visible.</text>',
        '<text x="30" y="267" font-family="sans-serif" font-size="2.4">Guides: cap height, x-height, baseline, descender. Use black or dark neutral ink.</text>',
        f'<text x="30" y="282" font-family="sans-serif" font-size="2.4">{reference.width:g} mm print reference</text>',
        f'<path d="M {reference.x} {reference.y} V {reference.y + reference.height} M {reference.x} {center_y} H {reference.x + reference.width} M {reference.x + reference.width} {reference.y} V {reference.y + reference.height}" stroke="black" stroke-width=".25"/>',
        "</svg>",
    ]
    result = "".join(values)
    if manifest.script_code == "Deva":
        result = (
            result.replace("LATIN ASCII 1.0.0", "DEVANAGARI ENGINEERING v1")
            .replace("of 2", "(engineering prompts)")
            .replace("One symbol per cell.", "Write the shown character or cluster.")
            .replace(
                "cap height, x-height, baseline, descender",
                "upper marks, headline, baseline, lower marks",
            )
        )
    return result
