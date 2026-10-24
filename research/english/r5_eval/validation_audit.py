"""Audit saved R4 VALIDATION predictions and crop geometry only; no model execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

from PIL import Image

from research.english.r5_data.common import (
    checked,
    digest_file,
    image_identity,
    read_jsonl,
    write_json,
)
from research.english.r5_eval.scoring import score_predictions


def audit(staging: Path, corrected_report: Path, output: Path) -> dict:
    report = json.loads(corrected_report.read_text())
    if report.get("stage") != "trocr_full_pass" or report.get("test_evaluated") is not False:
        raise ValueError("r5_findings_require_validation_report_only")
    repair = report["scoring_repair"]
    path = Path(repair["predictions_path"])
    if path.is_symlink() or digest_file(path) != repair["predictions_sha256"]:
        raise ValueError("r5_validation_prediction_pin_changed")
    # The metadata is filtered before predictions are read; test predictions are never opened.
    rows = [
        row for row in read_jsonl(checked(staging, "study.jsonl")) if row["split"] == "validation"
    ]
    metrics = score_predictions(rows, read_jsonl(path))
    expected = report["candidates"][0]["validation_metrics"]
    if (metrics["n"], metrics["correct"]) != (expected["n"], expected["correct"]):
        raise ValueError("r5_recomputed_validation_disagrees_with_frozen_report")
    geometry = []
    for row in rows:
        with Image.open(checked(staging, row["path"])) as image:
            identity = image_identity(image)
        geometry.append(
            {"id": row["id"], "label": row["label"], "writer": row["writer"], **identity}
        )
    by_case = {}
    for case in ("uppercase", "lowercase"):
        group = [r for r in geometry if r["label"].isupper() == (case == "uppercase")]
        by_case[case] = {
            key: {
                "minimum": min(r[key] for r in group),
                "median": median(r[key] for r in group),
                "maximum": max(r[key] for r in group),
            }
            for key in ("ink_width_px", "ink_height_px", "ink_pixels")
        }
    result = {
        "version": "r5-validation-findings-v1",
        "source_report_sha256": digest_file(corrected_report),
        "prediction_sha256": digest_file(path),
        "validation_mapping_sha256": digest_file(staging / "study.jsonl"),
        "metrics": metrics,
        "geometry_by_case": by_case,
        "canvas": [128, 128],
        "r4_resize": [384, 384],
        "resize_audit": "R4 preserves the full square source canvas then resizes both axes by 3; it does not tight-crop ink. Relative ink extent within this source crop is retained. Interpolation changes raster pixels; quality impact is unmeasured.",
        "tight_crop_audit": "Tight per-letter normalization would discard observed margins and ink size within the 128px crop. R5 retains publisher bytes. Normalized duplicate hashes are audit-only, never model inputs.",
        "unavailable": [
            "original page baseline",
            "physical glyph size",
            "common page scale",
            "publisher transformation before these crops",
        ],
        "recommendations_from_validation_only": [
            "Preserve A-Z and a-z labels and primary raw exact scoring; never repair case by dictionary or language context.",
            "Keep balanced case/letter coverage while expanding examples from previously trained writers and retaining validation writer separation.",
            "Prioritize an explicitly case-sensitive training objective and compare candidates on validation only; 328 case errors motivate the experiment, not an accuracy forecast.",
            "Retain full crop margins and measured within-crop ink extent; do not invent page baseline, x-height or relative physical size.",
            "Freeze the validation winner before using never-exposed test writers; historical test outcomes are evidence only.",
        ],
        "historical_test_predictions_opened": False,
        "model_execution": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--corrected-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.staging, args.corrected_report, args.output)
    print(
        json.dumps(
            {
                "validation_n": result["metrics"]["n"],
                "correct": result["metrics"]["correct"],
                "case_only_errors": result["metrics"]["case_only_errors"],
                "geometry_by_case": result["geometry_by_case"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
