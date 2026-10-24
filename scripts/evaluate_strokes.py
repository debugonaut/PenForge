"""Analytic geometric controls for Track C, never representative handwriting."""

import argparse
import hashlib
import json
import math
import statistics
import tempfile
from pathlib import Path

import numpy as np
from penforge_contracts import GlyphOutline, StrokeDocument, content_hash

from packages.extraction.geometry import outline_svg, rasterize_outline
from packages.extraction.isolated import analyze_isolated

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = ROOT / args.output
    if out.exists():
        raise ValueError("use_new_attempt_path")
    out.parent.mkdir(parents=True, exist_ok=True)
    paths = [
        [(0.2, 0.2), (0.7, 0.2), (0.7, 0.6), (0.2, 0.6), (0.2, 0.2)],
        [(0.75, 0.4), (0.75, 0.9)],
        [(0.85, 0.15)],
    ]
    records = []
    for pointer in ("mouse", "pen"):
        time = 0.0
        strokes = []
        for index, points in enumerate(paths):
            values = []
            for x, y in points:
                values.append(
                    dict(
                        x=x,
                        y=y,
                        t_ms=time,
                        **(
                            dict(pressure=0.5, tilt_x=10.0, tilt_y=-5.0, twist=20.0)
                            if pointer == "pen"
                            else {}
                        ),
                    )
                )
                time += 10
            strokes.append(dict(stroke_id=f"s{index}", pointer_type=pointer, points=values))
        document = StrokeDocument.model_validate(
            dict(
                schema_version=1,
                coordinate_system=dict(
                    origin="top_left", x_positive="right", y_positive="down", normalized=True
                ),
                capture_size_px=dict(width=100, height=120),
                timing="observed",
                strokes=strokes,
                operations=[],
            )
        )
        data = document.model_dump_json().encode()
        (out.parent / f"{pointer}-source.json").write_bytes(data)
        config = dict(
            source_type="canvas_drawing",
            source_hash="sha256:" + hashlib.sha256(data).hexdigest(),
            assignment=dict(
                codepoints=[103],
                capture_transform=dict(
                    schema_version=1,
                    units_per_em=1000,
                    baseline_y=0.6,
                    units_per_px=7.0,
                    origin_x_px=0.0,
                    pen_width_px=3.0,
                ),
            ),
        )
        repeats = []
        for _ in range(3):
            with tempfile.TemporaryDirectory(prefix="penforge-eval-", dir="/private/tmp") as tmp:
                work = Path(tmp)
                (work / "source").write_bytes(data)
                result = analyze_isolated(work, config, cancel_check=lambda: None)
                shape = GlyphOutline.model_validate_json(
                    (work / result["candidates"][0]["outline"]).read_bytes()
                )
                repeats.append(
                    dict(hash=content_hash(shape.model_dump(mode="json")), **result["resources"])
                )
        assert len({r["hash"] for r in repeats}) == 1
        assert document.model_dump_json().encode() == data
        (out.parent / f"{pointer}-outline.json").write_text(shape.model_dump_json())
        (out.parent / f"{pointer}-outline.svg").write_text(outline_svg(shape))
        # Independent analytic distance to closed line segments with a constant
        # radius brush. Its pixel-center samples are not the rasterizer's circles.
        yy, xx = np.mgrid[:480, :400]
        grid = np.stack([xx / 4, yy / 4], axis=-1)
        distance = np.full((480, 400), np.inf)
        for points in paths:
            pts = [np.array([x * 100, y * 120]) for x, y in points]
            segments = list(zip(pts, pts[1:])) or [(pts[0], pts[0])]
            for a, b in segments:
                chord = b - a
                den = float(np.dot(chord, chord))
                t = (
                    np.clip(np.sum((grid - a) * chord, axis=-1) / den, 0, 1)
                    if den
                    else np.zeros((480, 400))
                )
                distance = np.minimum(
                    distance, np.linalg.norm(grid - (a + t[:, :, None] * chord), axis=-1)
                )
        target = distance <= 1.5
        actual = (
            rasterize_outline(shape, (480, 400), baseline_px=0.6 * 120 * 4, units_per_px=7 / 4) > 0
        )
        iou = np.count_nonzero(target & actual) / np.count_nonzero(target | actual)
        points = [s.to for c in shape.contours for s in c.segments]
        actual_bounds = [
            min(p.x for p in points) / 7,
            max(p.x for p in points) / 7,
            0.6 * 120 - max(p.y for p in points) / 7,
            0.6 * 120 - min(p.y for p in points) / 7,
        ]
        expected_bounds = [18.5, 86.5, 16.5, 109.5]
        records.append(
            dict(
                pointer_type=pointer,
                source_hash=config["source_hash"],
                source_document_hash=content_hash(document.model_dump(mode="json")),
                outline_hash=repeats[0]["hash"],
                source_unchanged=True,
                observed_pressure=pointer == "pen",
                analytic_capsule_mask_iou=float(iou),
                maximum_bounds_error_capture_px=max(
                    abs(a - b) for a, b in zip(actual_bounds, expected_bounds)
                ),
                contours=len(shape.contours),
                nodes=sum(len(c.segments) + 1 for c in shape.contours),
                repetitions=repeats,
                p50_wall_seconds=statistics.median(r["wall_seconds"] for r in repeats),
                maximum_child_rss_bytes=max(r["observed_peak_rss_bytes"] for r in repeats),
                provenance=result["quality"],
                image_segmentation=False,
                ocr=False,
                human_corrections=None,
                human_seconds=None,
            )
        )
    sources = [
        Path(__file__),
        *sorted((ROOT / "packages/extraction").glob("*.py")),
        *sorted((ROOT / "packages/contracts/penforge_contracts").glob("*.py")),
    ]
    hashes = {
        str(p.relative_to(ROOT)): "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sources
    }
    for p in sources:
        copy = out.parent / "code" / p.relative_to(ROOT)
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(p.read_bytes())
    report = dict(
        schema_version=1,
        date="2026-10-20",
        domain="analytic_digital_controls_only",
        source_files=hashes,
        source_tree_hash=content_hash(hashes),
        dependency_lock_hash="sha256:"
        + hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        command=[".venv/bin/python", "scripts/evaluate_strokes.py", "--output", args.output],
        records=records,
        production_validated=False,
        limitations=[
            "Constant-width analytic controls do not represent real pen ink or handwriting.",
            "Pressure variation, hardware calibration and human correction time remain unmeasured.",
            "Three local isolated-process repeats do not establish hosted performance.",
        ],
    )
    out.write_text(json.dumps(report, indent=2) + "\n")
    gates = dict(
        schema_version=1,
        domain=report["domain"],
        baseline_hash="sha256:" + hashlib.sha256(out.read_bytes()).hexdigest(),
        minimum_analytic_mask_iou=math.floor(
            min(r["analytic_capsule_mask_iou"] for r in records) * 1e6
        )
        / 1e6,
        maximum_bounds_error_capture_px=max(r["maximum_bounds_error_capture_px"] for r in records),
        required_contours=records[0]["contours"],
        required_metadata_immutability=True,
        release_status="synthetic_geometry_regression_only",
    )
    out.with_name("gates.v1.json").write_text(json.dumps(gates, indent=2) + "\n")
    print(json.dumps(gates))


if __name__ == "__main__":
    main()
