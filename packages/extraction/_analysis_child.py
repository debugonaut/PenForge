"""Private fixed-code entry point; no credentials/network and bounded file output."""

import hashlib
import json
import resource
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "packages/contracts")]
from packages.capture_safety._sandbox import install_and_check_network_deny  # noqa: E402


def main():
    config = json.loads(sys.stdin.buffer.read(2_000_001))
    isolation = install_and_check_network_deny()
    resource.setrlimit(resource.RLIMIT_CPU, (config["cpu_seconds"], config["cpu_seconds"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (96, 96))
    import cv2
    from penforge_contracts import StrokeDocument

    from packages.extraction.geometry import vectorize_strokes
    from packages.extraction.images import extract_image

    cv2.setNumThreads(1)
    data = Path("source").read_bytes()
    if "sha256:" + hashlib.sha256(data).hexdigest() != config["source_hash"]:
        raise ValueError("source_hash_mismatch")
    files: list[dict[str, Any]] = []

    def save(data, kind, mime):
        name = f"output-{len(files)}"
        Path(name).write_bytes(data)
        item = dict(
            name=name,
            kind=kind,
            media_type=mime,
            size_bytes=len(data),
            sha256="sha256:" + hashlib.sha256(data).hexdigest(),
        )
        files.append(item)
        return name

    def outline(value):
        return save(value.model_dump_json().encode(), "vector", "application/json")

    candidates = []
    source_type = config["source_type"]
    if source_type == "canvas_drawing":
        try:
            mode = config.get("drawing_mode")
            converter = vectorize_strokes
            if mode == "fixed_round_v1":
                from packages.extraction.round_strokes import vectorize_round_strokes

                converter = vectorize_round_strokes
            elif mode is not None:
                raise ValueError("unsupported_drawing_mode")
            shape, quality = converter(
                StrokeDocument.model_validate_json(data),
                config["assignment"]["codepoints"],
                config["assignment"]["capture_transform"],
            )
            candidates.append(
                dict(
                    codepoints=list(shape.codepoints),
                    outline=outline(shape),
                    flags=["identity_unverified", *quality.get("flags", [])],
                    provenance=quality,
                )
            )
            state, reasons = "analyzed", []
        except ValueError as error:
            state, reasons = (
                "needs_better_input",
                [
                    str(error)
                    if str(error)
                    in {
                        "capture_dimensions_required",
                        "stroke_geometry_budget_exceeded",
                        "capture_raster_budget_exceeded",
                        "empty_ink",
                        "derived_stroke_outside_capture",
                    }
                    else "isolated_drawing_required"
                ],
            )
            quality = {"image_segmentation": False, "ocr": False}
    elif source_type in {"template_scan", "template_photo"}:
        result = extract_image(
            data, config["manifest"], config["manifest_hash"], config["image_config"]
        )
        quality = result.quality
        state, reasons = (
            ("needs_better_input", list(result.reasons)) if result.reasons else ("analyzed", [])
        )
        if result.normalized_png:
            quality["normalized_artifact"] = save(
                result.normalized_png, "preprocessed", "image/png"
            )
        for glyph in result.glyphs:
            candidates.append(
                dict(
                    codepoints=glyph.codepoints,
                    cell_id=glyph.cell_id,
                    outline=outline(glyph.outline),
                    flags=list(glyph.flags),
                    crop_artifact=save(glyph.crop_png, "segment", "image/png"),
                    mask_artifact=save(glyph.mask_png, "segment", "image/png"),
                )
            )
    else:
        state, reasons = "needs_better_input", ["isolated_samples_required"]
        quality = {"natural_cursive_status": "experimental_unavailable", "ocr": False}
    report = dict(
        state=state,
        reasons=reasons,
        quality=quality,
        candidates=candidates,
        files=files,
        isolation=isolation,
    )
    encoded = json.dumps(report, allow_nan=False).encode()
    if len(encoded) > 2_000_000 or sum(x["size_bytes"] for x in files) > 32 * 1024 * 1024:
        raise ValueError("artifact_budget_exceeded")
    Path("result.json").write_bytes(encoded)
    print('{"ok":true}')


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"ok":false}')
        sys.exit(1)
