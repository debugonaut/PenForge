"""Run inside the built images, with no network and an external memory cap."""

import argparse
import copy
import io
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from penforge_contracts import GlyphOutline, StrokeDocument
from penforge_contracts.unicode import graphemes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    root = Path("packages/contracts")
    assert graphemes("A\u0301") == [[65, 769]]
    assert len(graphemes("कि")) == 1
    StrokeDocument.model_validate(json.loads((root / "fixtures/strokes.v1.json").read_text()))
    outline = json.loads((root / "fixtures/outline.v1.json").read_text())
    GlyphOutline.model_validate(outline)
    if args.worker:
        from penforge_contracts import FontBuildManifest
        from penforge_font_engine.compiler import compile_font, installed_versions
        from penforge_font_engine.validation import validate_font
        from PIL import Image, ImageDraw

        from packages.capture_safety import DecodeLimits, verify_image
        from packages.extraction import extract_image, load_template, template_hash, template_png
        from packages.extraction.templates import PIXELS_PER_MM

        # Real parsers, synthetic ink, no external data or network.
        template = load_template(0)
        page = Image.open(io.BytesIO(template_png(template))).convert("RGB")
        for cell in template.cells[:3]:
            r = cell.rect_mm
            x, y = round(r.x * PIXELS_PER_MM), round(r.y * PIXELS_PER_MM)
            ImageDraw.Draw(page).line(
                (x + 40, y + 40, x + 70, y + 110, x + 110, y + 50), fill="black", width=5
            )
        encoded = io.BytesIO()
        page.save(encoded, format="PNG")
        source = encoded.getvalue()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.png"
            path.write_bytes(source)
            verify_image(
                path,
                declared_mime="image/png",
                output_dir=Path(temporary),
                limits=DecodeLimits(),
                cancel_check=lambda: None,
            )
            extraction = extract_image(source, template, template_hash(template))
            assert not extraction.reasons and extraction.glyphs
        rows = []
        for cp in (32, 65, 86):
            glyph = copy.deepcopy(outline)
            glyph["codepoints"] = [cp]
            if cp == 32:
                glyph["contours"] = []
                glyph["metrics"] = {"advance_width": 250.0, "left_side_bearing": 0.0}
            elif cp == 86:
                glyph["metrics"]["advance_width"] += 100
            rows.append(
                dict(outline=glyph, approval="approved", origin="user_capture", variant_index=0)
            )
        manifest = FontBuildManifest(
            snapshot_hash="sha256:" + "a" * 64,
            snapshot_created_at=datetime(2026, 10, 21, tzinfo=UTC),
            family_name="Penforge Image Smoke",
            tool_versions=installed_versions(),
            options={"spacing": 100},
        )
        with tempfile.TemporaryDirectory() as temporary:
            for format in ("ttf", "otf", "woff2"):
                data = compile_font(manifest, rows, format)
                report = validate_font(data, manifest, rows, format, Path(temporary) / format)
                assert report["status"] in {"passed", "passed_with_warnings"}, report
                assert report["layers"]["harfbuzz"]["measured_spacing"]["corrected_pairs"] > 0
    print("Packaged contracts" + (" and capped font pipeline" if args.worker else "") + " passed.")


if __name__ == "__main__":
    main()
