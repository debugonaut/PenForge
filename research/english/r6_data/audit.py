"""Deterministic private R5 train/validation image audit. No model execution."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

from research.english.r5_data.common import (
    canonical_hash,
    checked,
    digest_file,
    read_jsonl,
    write_json,
)
from research.english.r5_eval.lifecycle import verify_frozen
from research.english.r6_data.prepare import verify_original
from research.english.r6_eval.scoring import paired_comparison, score_predictions
from research.pretrained.artifacts import ROOT


def prepare_audit(staging: Path, r5_run: Path, output: Path) -> dict:
    if output.is_symlink():
        raise ValueError("r6_audit_symlink_output")
    staging, r5_run, output = staging.resolve(), r5_run.resolve(), output.resolve()
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "research/runs"):
        raise ValueError("r6_private_audit_must_be_external_or_ignored")
    if output.exists():
        raise ValueError("r6_audit_output_must_be_new")
    session = json.loads(checked(r5_run, "session.json").read_text())
    verify_frozen(checked(r5_run, "winner-frozen.json"), session)
    verify_original(staging)
    if digest_file(checked(staging, "preparation.json")) != session["data_preparation_sha256"]:
        raise ValueError("r6_audit_staging_differs_from_frozen_r5")
    output.mkdir(parents=True, mode=0o700)
    rows = read_jsonl(checked(staging, "validation.jsonl"))
    train = read_jsonl(checked(staging, "train.jsonl"))
    paths = [
        r5_run / f"training/pass-{p:04d}-validation-predictions-private.jsonl" for p in (1, 2, 3)
    ]
    predictions = [read_jsonl(p) for p in paths]
    scores = [score_predictions(rows, records) for records in predictions]
    maps = [{r["sample_id"]: r["prediction"] for r in records} for records in predictions]
    strata: dict[str, list[dict]] = {
        key: [] for key in ("persistent_error", "regressed", "fixed", "correct_control")
    }
    for row in rows:
        good = [p[row["id"]] == row["label"] for p in maps]
        if not good[1] and good[2]:
            group = "fixed"
        elif good[1] and not good[2]:
            group = "regressed"
        elif not any(good):
            group = "persistent_error"
        elif all(good):
            group = "correct_control"
        else:
            continue
        strata[group].append(row)
    chosen: list[tuple[str, dict]] = []
    for group, pool in strata.items():
        ordered = sorted(pool, key=lambda r: canonical_hash([20261023, group, r["id"]]))
        shape = [r for r in ordered if r["label"] in "IliL" or maps[2][r["id"]] in "IliL"][:4]
        chosen.extend((group, r) for r in (shape + [r for r in ordered if r not in shape])[:12])
    chosen.extend(
        ("training_pixels_only", r)
        for r in sorted(train, key=lambda r: canonical_hash([20261023, "train", r["id"]]))[:24]
    )
    chosen.sort(key=lambda item: canonical_hash([20261023, "blind", item[1]["id"]]))
    manifest: list[dict] = []
    for index, (group, row) in enumerate(chosen, 1):
        manifest.append(
            {
                "blind_index": index,
                "stratum": group,
                "row": row,
                "saved_predictions": None
                if row["split"] == "train"
                else [p[row["id"]] for p in maps],
                "image_sha256": digest_file(checked(staging, row["path"])),
            }
        )
    write_json(output / "reveal-private.json", manifest)
    for offset in range(0, len(chosen), 36):
        sheet = Image.new("RGB", (6 * 150, 6 * 155), "#dedbd4")
        draw = ImageDraw.Draw(sheet)
        for local, item in enumerate(manifest[offset : offset + 36]):
            x, y = (local % 6) * 150, (local // 6) * 155
            with Image.open(checked(staging, item["row"]["path"])) as image:
                sheet.paste(image.convert("RGB"), (x + 11, y + 20))
            draw.text((x + 11, y + 4), f"{item['blind_index']:03d}", fill="black")
        sheet.save(output / f"blind-{offset // 36 + 1}.png")
    geometry = []
    for row in train + rows:
        with Image.open(checked(staging, row["path"])) as image:
            gray = image.convert("L")
            mask = gray.point(lambda n: 255 if n < 128 else 0)
            bbox = mask.getbbox()
            if bbox is None:
                raise ValueError("r6_blank_audit_crop")
            geometry.append(
                {
                    "id": row["id"],
                    "split": row["split"],
                    "label": row["label"],
                    "width": bbox[2] - bbox[0],
                    "height": bbox[3] - bbox[1],
                    "minimum_margin": min(bbox[0], bbox[1], 128 - bbox[2], 128 - bbox[3]),
                    "ink_fraction": mask.histogram()[255] / 16384,
                    "minimum_gray": gray.getextrema()[0],
                }
            )
    summary = {}
    for split in ("train", "validation"):
        geometry_group = [r for r in geometry if r["split"] == split]
        summary[split] = {
            "n": len(geometry_group),
            "border_touch": sum(r["minimum_margin"] == 0 for r in geometry_group),
            "within_2px_border": sum(r["minimum_margin"] <= 2 for r in geometry_group),
            "no_dark_pixels_below_64": sum(r["minimum_gray"] >= 64 for r in geometry_group),
            "median_ink_fraction": statistics.median(r["ink_fraction"] for r in geometry_group),
            "by_case": {
                case: {
                    key: statistics.median(
                        r[key] for r in geometry_group if r["label"].isupper() == upper
                    )
                    for key in ("width", "height", "minimum_margin")
                }
                for case, upper in (("upper", True), ("lower", False))
            },
        }
    write_json(output / "geometry-private.json", geometry)
    paired = paired_comparison(rows, predictions[1], predictions[2])
    write_json(output / "validation-private.json", {"scores": scores, "pass2_to_3": paired})
    result = {
        "version": "r6-private-error-audit-v1",
        "model_execution": False,
        "sample_count": len(chosen),
        "selection_seed": 20261023,
        "sampling": "12 per stratum, up to 4 I/l/i/L-priority then hash order; 24 hash-ordered training crops; shuffled blind indices",
        "stratum_pool_counts": {key: len(pool) for key, pool in strata.items()},
        "selected_strata": dict(Counter(group for group, _ in chosen)),
        "prediction_files": {str(p): digest_file(p) for p in paths},
        "geometry": summary,
        "training_predictions": "unavailable; training loss logs are optimizer-step aggregates, not per-image recognition",
        "human_adjudication": False,
        "benchmark_truth_changed": False,
    }
    write_json(output / "summary.json", result)
    for file in output.iterdir():
        file.chmod(0o600)
    return result


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("staging", "r5-run", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    result = prepare_audit(args.staging, args.r5_run, args.output)
    print(
        json.dumps(
            {
                "status": "private_audit_prepared",
                "samples": result["sample_count"],
                "model_execution": False,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
