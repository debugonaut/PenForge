"""Reproduce Phase04 engineering baselines; no training or real handwriting claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import resource
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from penforge_contracts.serialization import content_hash

from packages.extraction import (
    extract_image,
    load_template,
    rasterize_outline,
    template_hash,
    template_svg,
)
from packages.extraction.fixtures import FONT_HASH, LICENSE_HASH, perturb_sheet, synthetic_sheet

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def iou(a, b) -> float:
    aa = a > 127
    bb = b > 127
    union = int(np.count_nonzero(aa | bb))
    return float(np.count_nonzero(aa & bb) / union) if union else 1.0


def boundary_f1(a, b) -> float:
    aa = (a > 127).astype(np.uint8)
    bb = (b > 127).astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    ae = aa - cv2.erode(aa, kernel)
    be = bb - cv2.erode(bb, kernel)
    precision = np.count_nonzero(ae & cv2.dilate(be, kernel)) / max(1, np.count_nonzero(ae))
    recall = np.count_nonzero(be & cv2.dilate(ae, kernel)) / max(1, np.count_nonzero(be))
    return float(2 * precision * recall / (precision + recall)) if precision + recall else 0.0


def validate_fixture_configuration(config: dict) -> None:
    """All variants of this single source family remain validation-only."""
    if (
        config["font_hash"] != "sha256:" + FONT_HASH
        or config["license_hash"] != "sha256:" + LICENSE_HASH
    ):
        raise ValueError("synthetic_source_configuration_mismatch")
    groups = [group for values in config["splits"].values() for group in values]
    if len(groups) != len(set(groups)):
        raise ValueError("synthetic_font_split_leakage")
    if (
        set(config["splits"]) != {"train", "validation", "test"}
        or config["splits"]["validation"] != ["caveat-all-weights-global"]
        or config["font_family_surrogate"] != "caveat-all-weights-global"
        or config["splits"]["train"]
        or config["splits"]["test"]
    ):
        raise ValueError("single_font_requires_validation_only")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/phase04-core/baseline.v1.json")
    parser.add_argument("--config", default="research/evaluation/fixtures/phase04/config.v1.json")
    args = parser.parse_args()
    out = ROOT / args.output
    if out.exists():
        raise ValueError("evaluation_output_exists_use_new_attempt_path")
    out.parent.mkdir(parents=True, exist_ok=True)
    config_path = ROOT / args.config
    config = json.loads(config_path.read_text())
    validate_fixture_configuration(config)
    outputs = []
    all_counts = 0
    for page in (0, 1):
        manifest = load_template(page)
        (out.parent / f"template-page-{page}.svg").write_text(template_svg(manifest))
        for condition in config["conditions"]:
            source, ground = synthetic_sheet(
                manifest, missing_codepoints=tuple(condition.get("missing_codepoints", ()))
            )
            distorted = perturb_sheet(source, condition, seed=config["seed"])
            (out.parent / f"synthetic-page-{page}-{condition['name']}.png").write_bytes(distorted)
            nonempty = {cell_id for cell_id, mask in ground.items() if np.any(mask > 127)}
            for method in config["methods"]:
                options = {key: value for key, value in method.items() if key != "id"}
                if config.get("quality_gates") is not None:
                    options["quality_gates"] = config["quality_gates"]
                elapsed_runs = []
                geometry_hashes = []
                for _ in range(config.get("repetitions", 1)):
                    begin = time.perf_counter()
                    result = extract_image(distorted, manifest, template_hash(manifest), options)
                    elapsed_runs.append((time.perf_counter() - begin) * 1000)
                    geometry_hashes.append(
                        content_hash([g.outline.model_dump(mode="json") for g in result.glyphs])
                    )
                assert len(set(geometry_hashes)) == 1
                mapping = {glyph.cell_id: glyph for glyph in result.glyphs}
                found = set(mapping)
                crop_ious = []
                boundaries = []
                vector_ious = []
                nodes = []
                flags = 0
                for cell in manifest.cells:
                    glyph = mapping.get(cell.cell_id)
                    if glyph is None:
                        continue
                    mask = cv2.imdecode(np.frombuffer(glyph.mask_png, np.uint8), 0)
                    target = ground[cell.cell_id]
                    crop_ious.append(iou(mask, target))
                    boundaries.append(boundary_f1(mask, target))
                    guide = next(g for g in manifest.guides if g.guide_id == cell.guide_id)
                    reconstructed = rasterize_outline(
                        glyph.outline,
                        mask.shape,
                        baseline_px=guide.baseline_offset_mm * 8,
                        units_per_px=50 / 8,
                    )
                    vector_ious.append(iou(mask, reconstructed))
                    nodes.append(sum(len(c.segments) + 1 for c in glyph.outline.contours))
                    flags += any(flag != "identity_unverified" for flag in glyph.flags)
                record = {
                    "page": page,
                    "condition": condition["name"],
                    "method": method["id"],
                    "expected_nonempty": len(nonempty),
                    "returned": len(found),
                    "hit_rate": len(found & nonempty) / max(1, len(nonempty)),
                    "false_glyph_count": len(found - nonempty),
                    "missed_glyph_count": len(nonempty - found),
                    "cell_mask_iou_mean": statistics.mean(crop_ious) if crop_ious else None,
                    "cell_mask_iou_min": min(crop_ious) if crop_ious else None,
                    "boundary_f1_1px_mean": statistics.mean(boundaries) if boundaries else None,
                    "vector_raster_iou_mean": statistics.mean(vector_ious) if vector_ious else None,
                    "vector_raster_iou_min": min(vector_ious) if vector_ious else None,
                    "node_count_max": max(nodes) if nodes else None,
                    "retake_reasons": list(result.reasons),
                    "flagged_cells_requiring_extra_inspection": flags,
                    "identity_reviews_required": len(found),
                    "actual_human_corrections": None,
                    "human_seconds": None,
                    "extraction_repetitions": len(elapsed_runs),
                    "extraction_p50_ms": statistics.median(elapsed_runs),
                    "extraction_p95_ms": sorted(elapsed_runs)[
                        math.ceil(0.95 * len(elapsed_runs)) - 1
                    ],
                    "process_lifetime_peak_rss_mb": resource.getrusage(
                        resource.RUSAGE_SELF
                    ).ru_maxrss
                    / (1024**2 if sys.platform == "darwin" else 1024),
                    "bytes_in": len(distorted),
                    "outline_bytes": sum(len(g.outline.model_dump_json()) for g in result.glyphs),
                    "source_sha256": "sha256:" + hashlib.sha256(distorted).hexdigest(),
                    "ancestor_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
                    "outline_set_hash": geometry_hashes[0],
                    "measurements": {
                        k: result.quality.get(k)
                        for k in (
                            "before",
                            "after_rectification",
                            "effective_pixels_per_mm",
                            "marker_reprojection_rms_px",
                        )
                    },
                }
                outputs.append(record)
                all_counts += len(found)
                print(
                    f"page={page} condition={condition['name']} method={method['id']} candidates={len(found)} retake={list(result.reasons)}",
                    flush=True,
                )
    sources = (
        sorted((ROOT / "packages/extraction").glob("*.py"))
        + sorted((ROOT / "packages/contracts/penforge_contracts").glob("*.py"))
        + sorted((ROOT / "packages/contracts/policies").glob("*.json"))
        + [Path(__file__)]
    )
    code = {str(path.relative_to(ROOT)): digest(path) for path in sources}
    report = {
        "schema_version": 1,
        "run_id": config["run_id"],
        "date": config.get("date", "2026-10-19"),
        "domain": "synthetic engineering validation only",
        "production_validated": False,
        "config_hash": digest(config_path),
        "configuration": config,
        "source_tree_hash": content_hash(code),
        "source_files": code,
        "dependency_lock_hash": digest(ROOT / "uv.lock"),
        "template_hashes": [template_hash(load_template(p)) for p in (0, 1)],
        "runtime": {
            "python": platform.python_version(),
            "opencv": cv2.__version__,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "command": [
            ".venv/bin/python",
            "scripts/evaluate_extraction.py",
            "--config",
            args.config,
            "--output",
            args.output,
        ],
        "records": outputs,
        "candidate_evaluations": all_counts,
        "recognition_accuracy": None,
        "writer_fidelity_human_score": None,
        "limitations": config["limitations"],
        "resource_limitations": [
            "n=3 is a local smoke profile, not throughput, concurrency or ETA.",
            "RSS is the lifetime process maximum across these cases, not a per-stage delta.",
        ],
    }
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    # Gates are derived only after the baseline and are for this engineering domain.
    qualified = [
        row
        for row in outputs
        if row["condition"]
        in {"clean", "perspective", "rotation", "lighting", "blur", "noise", "combined", "missing"}
        and row["method"] == config.get("selected_method", "bounded_cubic")
    ]
    successful = [row for row in qualified if not row["retake_reasons"]]
    gates = {
        "schema_version": 1,
        "date": config.get("date", "2026-10-19"),
        "domain": "synthetic regression only",
        "baseline_hash": digest(out),
        "minimum_cell_hit_rate": min(row["hit_rate"] for row in successful),
        "maximum_false_glyph_count": max(row["false_glyph_count"] for row in successful),
        "minimum_mean_cell_iou": math.floor(
            min(row["cell_mask_iou_mean"] for row in successful) * 1e6
        )
        / 1e6,
        "minimum_mean_vector_iou": math.floor(
            min(row["vector_raster_iou_mean"] for row in successful) * 1e6
        )
        / 1e6,
        "minimum_boundary_f1_1px": math.floor(
            min(row["boundary_f1_1px_mean"] for row in successful) * 1e6
        )
        / 1e6,
        "maximum_nodes": max(row["node_count_max"] for row in successful),
        "human_identity_review": "all_candidates",
        "real_handwriting_release_gate": "unmeasured",
        "selected_method": config.get("selected_method", "bounded_cubic"),
        "covered_conditions": sorted({row["condition"] for row in successful}),
        "unqualified_conditions": sorted(
            {row["condition"] for row in qualified if row["retake_reasons"]}
        ),
        "threshold_policy": "observed extrema with downward rounding at six decimal places; validation-only regression",
    }
    (out.parent / out.name.replace("baseline", "gates")).write_text(
        json.dumps(gates, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
