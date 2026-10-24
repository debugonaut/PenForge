"""OWNER-RUN ONLY: bounded R1 baselines, training and held-out evaluation.

Use PYTHONPATH=packages/contracts:. and the pinned worker/research environment.
Only audited external staging is read. Nothing enters product storage or a registry.
Execution requires --train. Agents must never invoke this training entrypoint.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import platform
import resource
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import PIL
from PIL import Image, ImageDraw

from research.evaluation.glyph_reuse import evaluate as evaluate_glyph_reuse
from research.evaluation.local_baselines import (
    FEATURE_VERSION,
    features,
    fit_ridge,
    load_tensors,
    nearest,
    predict_ridge,
    recognition_metrics,
    save_tensors,
)
from research.evaluation.local_baselines import recognition_metrics as scalar_metrics
from research.models import DatasetManifest
from research.preparation.archive_audit import ROOT, external, file_hash, sha
from research.preparation.check_study import verify_study
from research.preparation.freeze import validate_split_rows, writer_id
from research.validate import assert_build_eligible, digest, load_record


def rss_mb():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1e6 if sys.platform == "darwin" else 1e3)


def style_report(rows, descriptors, training_indices, config):
    roles = [i for i, row in enumerate(rows) if row.get("style_role")]
    if not roles:
        return {"status": "insufficient_shared_content", "generation": False}
    writers = sorted({rows[i]["writer"] for i in roles})
    mean = descriptors[training_indices].mean(axis=0)
    scale = descriptors[training_indices].std(axis=0).clip(1e-6)
    z = (descriptors - mean) / scale
    pools = {}
    for role in ("calibration", "query"):
        pools[role] = [
            np.array(
                sorted(
                    [i for i in roles if rows[i]["writer"] == w and rows[i]["style_role"] == role],
                    key=lambda i: rows[i]["label"],
                )
            )
            for w in writers
        ]

    def score(calibration, query):
        d = ((query[:, None] - calibration[None]) ** 2).mean(axis=2)
        return d.argmin(axis=1), d

    calibration = np.array([z[v].mean(axis=0) for v in pools["calibration"]])
    query = np.array([z[v].mean(axis=0) for v in pools["query"]])
    prediction, distances = score(calibration, query)
    correct = int((prediction == np.arange(len(writers))).sum())
    null = [
        sum(prediction[i] == p[i] for i in range(len(writers)))
        for p in itertools.permutations(range(len(writers)))
    ]
    rng, bootstrap = np.random.default_rng(config["seed"]), []
    for _ in range(config["style_bootstrap_repeats"]):
        sample = {}
        for role in ("calibration", "query"):
            n = len(pools[role][0])
            picks = rng.integers(0, n, size=n)
            sample[role] = np.array([z[v[picks]].mean(axis=0) for v in pools[role]])
        p, _ = score(sample["calibration"], sample["query"])
        bootstrap.append(float((p == np.arange(len(writers))).mean()))
    return {
        "status": "measured_raster_geometry_only",
        "n_writers": len(writers),
        "calibration_samples_per_writer": len(pools["calibration"][0]),
        "query_samples_per_writer": len(pools["query"][0]),
        "retrieval_accuracy": correct / len(writers),
        "correct": correct,
        "constant_reference_accuracy": 1 / len(writers),
        "exact_identity_permutation_p": sum(n >= correct for n in null) / len(null),
        "word_bootstrap_accuracy_p025_p975": np.quantile(bootstrap, [0.025, 0.975]).tolist(),
        "bootstrap_unit": "shared_words_not_independent_people",
        "descriptor_names": [
            "log_ink_aspect",
            "ink_fraction",
            "distance_transform_width_ratio",
            "ink_moment_lean_proxy",
        ],
        "training_only_standardization_mean": mean.tolist(),
        "training_only_standardization_std": scale.tolist(),
        "distance_matrix": distances.tolist(),
        "writers": [writer_id(w) for w in writers],
        "page_and_label_disjoint_halves": True,
        "human_fidelity": None,
        "learned_style_embedding": False,
        "font_generation": False,
        "limitations": [
            "Only three held-out provider writer groups; no population claim.",
            "Scanner, pen, word shape and thresholding can confound geometry.",
            "Word bootstrap is not a confidence interval over unseen people.",
        ],
    }


def comparison_sheets(rows, test_indices, predictions, train_indices, labels, staging, output):
    label_example: dict[str, int] = {}
    for i in train_indices:
        label_example.setdefault(rows[i]["label"], i)
    chosen = []
    for writer in sorted({rows[i]["writer"] for i in test_indices}):
        positions = [j for j, i in enumerate(test_indices) if rows[i]["writer"] == writer]
        # Show successes and failures, selected deterministically after evaluation.
        good = [
            j
            for j in positions
            if labels[predictions["ridge"][j]] == rows[test_indices[j]]["label"]
        ]
        bad = [j for j in positions if j not in good]
        chosen.extend((good[:2] + bad[:2])[:4])
    canvas = Image.new("RGB", (1560, 90 + 110 * len(chosen)), "#fffdf7")
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (12, 8), "Hindi word recognition: real held-out images; no generated glyphs", fill="black"
    )
    headings = [
        "Held-out writer input",
        "Pixel nearest train reference",
        "HOG nearest train reference",
        "Ridge label: train example (NOT generation)",
    ]
    for c, heading in enumerate(headings):
        draw.text((12 + 390 * c, 40), heading, fill="black")

    def paste(index, x, y):
        path = staging / "iiit-hw-dev-inner" / rows[index]["path"]
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((360, 72))
            canvas.paste(image, (x, y))

    for line, j in enumerate(chosen):
        i = test_indices[j]
        references = [
            i,
            train_indices[predictions["pixel_indices"][j]],
            train_indices[predictions["hog_indices"][j]],
            label_example[labels[predictions["ridge"][j]]],
        ]
        for c, ref in enumerate(references):
            x, y = 12 + 390 * c, 80 + 110 * line
            paste(ref, x, y)
            tag = (
                ("input " + writer_id(rows[i]["writer"]))
                if c == 0
                else ("correct label" if rows[ref]["label"] == rows[i]["label"] else "WRONG label")
            )
            draw.text((x, y + 76), tag, fill="black")
    canvas.save(output / "recognition-comparison.png")
    # Separate real-writer geometry comparisons; no font or synthesized text.
    style_writers = sorted({r["writer"] for r in rows if r.get("style_role")})
    if style_writers:
        sheet = Image.new("RGB", (1400, 60 + 170 * len(style_writers)), "white")
        d = ImageDraw.Draw(sheet)
        d.text(
            (10, 10),
            "Held-out Hindi writer geometry: calibration and query use different words/pages",
            fill="black",
        )
        for line, writer in enumerate(style_writers):
            d.text((10, 60 + line * 170), writer_id(writer), fill="black")
            for half, role in enumerate(("calibration", "query")):
                indices = [
                    i
                    for i, r in enumerate(rows)
                    if r["writer"] == writer and r.get("style_role") == role
                ][:4]
                d.text((210 + half * 590, 45 + line * 170), role, fill="black")
                for position, i in enumerate(indices):
                    with Image.open(staging / "iiit-hw-dev-inner" / rows[i]["path"]) as im:
                        image = im.convert("RGB")
                        image.thumbnail((270, 55))
                        sheet.paste(
                            image,
                            (
                                210 + half * 590 + (position % 2) * 280,
                                65 + line * 170 + (position // 2) * 65,
                            ),
                        )
        sheet.save(output / "style-comparison.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "research/configs/local-r1.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hardware-info", type=Path)
    parser.add_argument(
        "--train",
        action="store_true",
        help="Owner-run only: explicitly execute fitting and evaluation.",
    )
    args = parser.parse_args()
    if not args.train:
        parser.error(
            "No training executed. The owner must explicitly use --train. For preparation only, run research.preparation.check_study."
        )
    config = json.loads(args.config.read_text())
    start = time.monotonic()
    cv2.setNumThreads(config["threads"])
    np.random.seed(config["seed"])
    resource.setrlimit(
        resource.RLIMIT_CPU, (config["max_wall_seconds"], config["max_wall_seconds"])
    )

    def check():
        if time.monotonic() - start > config["max_wall_seconds"] or rss_mb() > config["max_rss_mb"]:
            raise RuntimeError("research_resource_budget_exceeded")

    def watchdog():
        while True:
            time.sleep(1)
            try:
                check()
            except RuntimeError:
                os._exit(124)

    threading.Thread(target=watchdog, daemon=True).start()
    staging = external(args.staging)
    output = args.output.resolve()
    # Artifacts may be private external storage or the repository's ignored run tree.
    if output.is_relative_to(ROOT) and not output.is_relative_to(ROOT / "research/runs"):
        raise ValueError("artifacts_must_use_ignored_run_directory")
    output.mkdir(parents=True, exist_ok=False)
    manifest = load_record(ROOT / "research/manifests/iiit-hw-dev-r1.yaml")
    if not isinstance(manifest, DatasetManifest):
        raise ValueError("expected_dataset_manifest")
    assert_build_eligible([manifest], "research_training")
    rows = [json.loads(line) for line in (staging / "study-r1.jsonl").read_text().splitlines()]
    validate_split_rows(rows)
    verify_study(rows, manifest, ROOT / "research/prepared/local-r1/split-ids.jsonl")
    pixel_rows, hog_rows, style_rows = [], [], []
    feature_started = time.monotonic()
    for i, row in enumerate(rows):
        check()
        path = staging / config["corpus"] / row["path"]
        data = path.read_bytes()
        if sha(data) != row["hash"]:
            raise ValueError("source_changed_after_admission")
        p, h, s = features(data)
        pixel_rows.append(p)
        hog_rows.append(h)
        style_rows.append(s)
        if i and i % 3000 == 0:
            print(json.dumps({"features_completed": i}), flush=True)
    pixels, hogs, styles = np.array(pixel_rows), np.array(hog_rows), np.array(style_rows)
    feature_seconds = time.monotonic() - feature_started
    indices = {
        s: np.array([i for i, r in enumerate(rows) if r["split"] == s and r["recognition"]])
        for s in ("train", "validation", "test")
    }
    training = indices["train"]
    labels = sorted({rows[i]["label"] for i in training})
    vocabulary = set(labels)
    label_map = {label: i for i, label in enumerate(labels)}
    y = np.array([label_map[rows[i]["label"]] for i in training], dtype=np.int32)
    timings, predictions = {}, {}
    report = {
        "run_id": "local-word-research-r1",
        "date": "2026-10-21",
        "seed": config["seed"],
        "config_hash": digest(config),
        "dataset_manifest_hash": digest(manifest),
        "split_ids_hash": "sha256:"
        + file_hash(ROOT / "research/prepared/local-r1/split-ids.jsonl"),
        "vocabulary_size": len(labels),
        "feature_version": FEATURE_VERSION,
        "recognition": {},
        "resources": {"feature_extraction_s": feature_seconds},
        "capability_status": "research_only",
        "product_integration": False,
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "opencv": cv2.__version__,
            "pillow": PIL.__version__,
            "os": platform.platform(aliased=True),
            "cpu": platform.processor(),
            "threads": config["threads"],
            "blas_thread_env": {
                k: os.environ.get(k)
                for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
            },
            "uv_lock_hash": "sha256:" + file_hash(ROOT / "uv.lock"),
        },
        "source_hashes": {
            str(p.relative_to(ROOT)): "sha256:" + file_hash(p)
            for p in sorted((ROOT / "research").rglob("*.py"))
        },
    }
    if args.hardware_info:
        hardware = json.loads(args.hardware_info.read_text())
        report["environment"].update(
            cpu=hardware["cpu"],
            ram_bytes=hardware["ram_bytes"],
            hardware_probe="separately_recorded_local_probe",
            hardware_record_hash="sha256:" + file_hash(args.hardware_info),
        )
    elif sys.platform == "darwin":
        try:
            report["environment"]["cpu"] = subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            report["environment"]["ram_bytes"] = int(
                subprocess.check_output(
                    ["/usr/sbin/sysctl", "-n", "hw.memsize"], text=True, stderr=subprocess.DEVNULL
                ).strip()
            )
        except (OSError, subprocess.CalledProcessError):
            report["environment"]["hardware_probe"] = "unavailable_in_sandbox"
    majority = Counter(y.tolist()).most_common(1)[0][0]

    def metrics_for(split, pred):
        truth = [rows[i]["label"] for i in indices[split]]
        words = [labels[int(p)] for p in pred]
        result = recognition_metrics(
            truth, words, [writer_id(rows[i]["writer"]) for i in indices[split]], vocabulary
        )
        from penforge_contracts.unicode import graphemes

        def clusters(text):
            return tuple(tuple(g) for g in graphemes(text))

        clustered = scalar_metrics(
            [clusters(t) for t in truth],
            [clusters(w) for w in words],
            [writer_id(rows[i]["writer"]) for i in indices[split]],
            {clusters(w) for w in vocabulary},
        )
        result["grapheme_cer"] = clustered["scalar_cer"]
        result["script_code"] = "Deva"
        result["language"] = "Hindi"
        result["calibrated_confidence"] = None
        return result

    # Reference baselines evaluated on validation before training is justified.
    valid = indices["validation"]
    for method, array in (("pixel_nearest", pixels), ("hog_nearest", hogs)):
        refs, elapsed = nearest(array[training], array[valid], config["nearest_batch_size"], check)
        report["recognition"].setdefault("validation", {})[method] = metrics_for(
            "validation", y[refs]
        )
        timings["validation_" + method] = elapsed
    report["recognition"]["validation"]["constant_word"] = metrics_for(
        "validation", [majority] * len(valid)
    )
    (output / "baseline-before-training.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"stage": "baseline_complete", "validation": report["recognition"]["validation"]}
        ),
        flush=True,
    )
    trained_at = time.monotonic()
    model = fit_ridge(hogs[training], y, len(labels), config["ridge_lambda"])
    report["resources"]["fit_wall_s"] = time.monotonic() - trained_at
    checkpoint = output / "ridge.safetensors"
    save_tensors(checkpoint, model)
    model = load_tensors(checkpoint)
    (output / "vocabulary-private.json").write_text(json.dumps(labels, ensure_ascii=False))
    report["checkpoint"] = {
        "sha256": file_hash(checkpoint),
        "bytes": checkpoint.stat().st_size,
        "format": "safetensors",
        "architecture": "512-feature multiclass ridge with intercept",
        "label_table_sha256": file_hash(output / "vocabulary-private.json"),
    }
    valid_prediction, elapsed = predict_ridge(model, hogs[valid], check=check)
    report["recognition"]["validation"]["ridge"] = metrics_for("validation", valid_prediction)
    timings["validation_ridge"] = elapsed
    train_prediction, _ = predict_ridge(model, hogs[training], check=check)
    report["recognition"]["train"] = {"ridge": metrics_for("train", train_prediction)}
    (output / "model-frozen-before-test.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps({"stage": "model_frozen", "fit_wall_s": report["resources"]["fit_wall_s"]}),
        flush=True,
    )
    # Final test is consumed only after the exact model and validation report are frozen.
    test = indices["test"]
    report["recognition"]["test"] = {"constant_word": metrics_for("test", [majority] * len(test))}
    for method, array in (("pixel_nearest", pixels), ("hog_nearest", hogs)):
        refs, elapsed = nearest(array[training], array[test], config["nearest_batch_size"], check)
        report["recognition"]["test"][method] = metrics_for("test", y[refs])
        predictions[method.split("_")[0] + "_indices"] = refs
        timings["test_" + method] = elapsed
    ridge_prediction, elapsed = predict_ridge(model, hogs[test], check=check)
    predictions["ridge"] = ridge_prediction
    report["recognition"]["test"]["ridge"] = metrics_for("test", ridge_prediction)
    timings["test_ridge"] = elapsed
    report["style"] = style_report(rows, styles, training, config)
    report["personal_baseline"] = evaluate_glyph_reuse(rows, staging, output, config)
    comparison_sheets(rows, test, predictions, training, labels, staging, output)
    report["resources"].update(
        wall_s=time.monotonic() - start,
        peak_rss_mb=rss_mb(),
        process_cpu_s=resource.getrusage(resource.RUSAGE_SELF).ru_utime
        + resource.getrusage(resource.RUSAGE_SELF).ru_stime,
        source_bytes=sum(r["size"] for r in rows),
        model_bytes=checkpoint.stat().st_size,
        batch_amortized_inference_ms={
            k: {"p50": float(np.median(v)), "p95": float(np.quantile(v, 0.95))}
            for k, v in timings.items()
        },
    )
    report["artifacts"] = {
        p.name: "sha256:" + file_hash(p) for p in sorted(output.iterdir()) if p.is_file()
    }
    report["limitations"] = [
        "Bounded subsample, 12 provider writers, unstratified mixed Hindi word crops only.",
        "Fixed training vocabulary; OOV cases count as errors. No line segmentation or open text decoding.",
        "No pretrained checkpoint was available in the authorized local inputs; no new model download.",
        "Validation and final test remain separate; the fixed recipe is not tuned on test results.",
        "Style geometry is not font fidelity, human preference, pressure, timing or pen direction.",
        "Authentic isolated-grapheme reuse is tested separately; no generated glyphs, validated personal font, product model, registration or promotion.",
        "Inference timings amortize batches; feature extraction is reported separately. No deployment inference claim.",
    ]
    (output / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"stage": "complete", "resources": report["resources"]}), flush=True)


if __name__ == "__main__":
    main()
