"""Record an existing local result. Never fits, evaluates or loads model weights.

Run IDs are immutable. Owner-run training is a separate command. This metadata
import verifies saved receipts, hashes and governance before writing public tables.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.models import BenchmarkRecord, DatasetManifest, ExperimentRecord, WriterRegistry
from research.preparation.archive_audit import ROOT, file_hash
from research.validate import audit, digest, load_record


def ref(identifier, checksum):
    return {"record_id": identifier, "content_hash": checksum}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--prefix", default="r1")
    args = p.parse_args()
    run = args.run.resolve()
    result = json.loads((run / "result.json").read_text())
    manifest = load_record(ROOT / "research/manifests/iiit-hw-dev-r1.yaml")
    registry = load_record(ROOT / "research/manifests/writers-v1.yaml")
    if not isinstance(manifest, DatasetManifest) or not isinstance(registry, WriterRegistry):
        raise ValueError("invalid_research_records")
    if result["dataset_manifest_hash"] != digest(manifest):
        raise ValueError("result_dataset_changed")
    config = json.loads((ROOT / "research/configs/local-r1.json").read_text())
    if result["config_hash"] != digest(config):
        raise ValueError("result_configuration_changed")
    for filename, checksum in result["artifacts"].items():
        if Path(filename).name != filename or "sha256:" + file_hash(run / filename) != checksum:
            raise ValueError("saved_run_artifact_changed")
    output = ROOT / "research/results/local-r1"
    output.mkdir(parents=True, exist_ok=False)
    (output / "result.json").write_bytes((run / "result.json").read_bytes())
    snapshot = run / "source-snapshot-receipt.json"
    receipt = json.loads(snapshot.read_text())
    if (
        receipt["changed_since_run"]
        or file_hash(run / "source-snapshot.zip") != receipt["snapshot_sha256"]
    ):
        raise ValueError("source_snapshot_integrity_failed")
    (output / "source-snapshot-receipt.json").write_bytes(snapshot.read_bytes())
    methods = {
        "constant": {"kind": "no_training", "prediction": "most_frequent_training_word"},
        "pixel": {
            "kind": "no_training",
            "reference": "all_frozen_training_samples",
            "features": "otsu_bbox_32x16_l2",
            "distance": "squared_euclidean",
        },
        "hog": {
            "kind": "no_training",
            "reference": "all_frozen_training_samples",
            "features": result["feature_version"],
            "distance": "squared_euclidean",
        },
        "ridge": {
            "kind": "custom_training",
            "architecture": result["checkpoint"]["architecture"],
            "lambda": config["ridge_lambda"],
            "vocabulary": "training_only",
            "target": "one_hot_word_label",
            "center": "training_only",
            "optimizer": "closed_form_numpy_solve",
            "checkpoint": "safetensors_f32",
        },
    }
    (output / "methods.json").write_text(json.dumps(methods, indent=2, sort_keys=True) + "\n")
    prefix = args.prefix
    dataset = ref(f"{manifest.dataset_id}:{manifest.version}", digest(manifest))
    method_refs = {
        name: ref(prefix + "-method-" + name, digest(body)) for name, body in methods.items()
    }
    model = ref(prefix + "-ridge-weights", "sha256:" + result["checkpoint"]["sha256"])
    code_ref = digest(result["source_hashes"])
    common = dict(
        created_on="2026-10-21",
        status="measured",
        measured_on="2026-10-21",
        script_code="Deva",
        style="mixed",
        condition="clean",
        evidence_domain="real_heldout",
        dataset_manifests=[dataset],
        split_registry_hash=digest(registry),
        config_hash=result["config_hash"],
        code_ref=code_ref,
        tool_versions={
            k: result["environment"][k] for k in ("python", "numpy", "opencv", "pillow")
        },
        runtime=result["environment"]["os"],
        cpu=result["environment"]["cpu"],
        ram_mb=result["environment"]["ram_bytes"] / 1e6,
        limitations=[
            "Research only; fixed bounded Hindi word study; no production approval.",
            "Twelve provider writer groups; mixed style is unstratified; annotations lack independent review.",
            "The original test writers were untouched during this first fit; future reproductions reuse an observed test set.",
            "Full metric breakdowns, failed methods and unavailable human/font dimensions are in the observed report.",
        ],
    )
    benchmarks = {}

    def benchmark(suffix, dimension, split, input_class, metrics, n, model_ref):
        record = BenchmarkRecord.model_validate(
            dict(
                common,
                record_id=prefix + "-" + suffix,
                dimension=dimension,
                split=split,
                stage=suffix,
                input_class=input_class,
                metrics=metrics,
                n=n,
                model_ref=model_ref,
            )
        )
        path = ROOT / "research/evaluation/benchmarks" / (record.record_id + ".yaml")
        if path.exists():
            raise ValueError("research_record_already_exists")
        path.write_text(json.dumps(record.model_dump(mode="json"), indent=2) + "\n")
        benchmarks[suffix] = record
        return ref(record.record_id, digest(record))

    def metric(name, value, n):
        return {"name": name, "unit": "fraction", "value": value, "n": n}

    def recognition_metrics(split, names):
        return [
            metric(
                name + "-" + key,
                result["recognition"][split][name][key],
                result["recognition"][split][name]["n"],
            )
            for name in names
            for key in ("wer", "scalar_cer", "grapheme_cer")
        ]

    baseline_ref = benchmark(
        "baseline-validation",
        "recognition",
        "validation",
        "cropped_word",
        recognition_metrics("validation", ["constant_word", "pixel_nearest", "hog_nearest"]),
        3000,
        ref(prefix + "-reference-methods", digest(methods)),
    )
    training_ref = benchmark(
        "ridge-validation",
        "recognition",
        "validation",
        "cropped_word",
        recognition_metrics("validation", ["ridge"]),
        3000,
        model,
    )
    heldout_ref = benchmark(
        "recognition-test",
        "recognition",
        "test",
        "cropped_word",
        recognition_metrics("test", ["constant_word", "pixel_nearest", "hog_nearest", "ridge"]),
        4500,
        model,
    )
    style = result["style"]
    style_ref = benchmark(
        "style-geometry-test",
        "writer_fidelity",
        "test",
        "content_controlled_word_geometry",
        [
            metric("raster-writer-retrieval", style["retrieval_accuracy"], style["n_writers"]),
            metric("constant-reference", style["constant_reference_accuracy"], style["n_writers"]),
        ],
        style["n_writers"],
        ref(prefix + "-geometry-descriptors", digest(style)),
    )
    personal = result["personal_baseline"]
    glyph_ref = benchmark(
        "glyph-reuse-test",
        "writer_fidelity",
        "test",
        "isolated_grapheme_crop_reuse",
        [
            metric(key.replace("_", "-"), value, personal["paired_graphemes"])
            for key, value in personal["means"].items()
        ],
        personal["paired_graphemes"],
        ref(prefix + "-authentic-reuse", digest(personal)),
    )
    artifacts = [
        ref(prefix + "-observed-result", "sha256:" + file_hash(output / "result.json")),
        ref(
            prefix + "-preparation",
            "sha256:" + file_hash(ROOT / "research/prepared/local-r1/preparation.json"),
        ),
        ref(
            prefix + "-archive-verification",
            "sha256:" + file_hash(ROOT / "research/prepared/local-r1/archive-verification.json"),
        ),
        ref(prefix + "-source-snapshot", "sha256:" + receipt["snapshot_sha256"]),
    ]
    experiment_common = dict(
        created_on="2026-10-21",
        status="completed",
        script_code="Deva",
        style="mixed",
        datasets=[dataset],
        artifacts=artifacts,
        code_ref=code_ref,
        environment_lock_hash=result["environment"]["uv_lock_hash"],
        config_hash=result["config_hash"],
        seed=result["seed"],
        command=[
            ".venv/bin/python",
            "-m",
            "research.evaluation.local_run",
            "--staging",
            "$PENFORGE_STAGING",
            "--output",
            "research/runs/r1/experiment-02",
            "--hardware-info",
            "research/runs/r1/hardware.json",
        ],
        capability_status="research_only",
        limitations=[
            "This run completed before the owner restricted training execution to them personally.",
            "Do not execute this historical command; current owner-run entrypoint additionally requires --train.",
            "No model registration, product inference, publication or deployment occurred.",
            "Checkpoint reproducibility is specified and byte-hashed; a second full fit was not performed.",
        ],
    )
    experiments = {}

    def experiment(suffix, stage, **fields):
        record = ExperimentRecord.model_validate(
            dict(experiment_common, record_id=prefix + "-" + suffix, stage=stage, **fields)
        )
        path = ROOT / "research/experiments" / (record.record_id + ".yaml")
        if path.exists():
            raise ValueError("research_record_already_exists")
        path.write_text(json.dumps(record.model_dump(mode="json"), indent=2) + "\n")
        experiments[suffix] = record
        return ref(record.record_id, digest(record))

    data_ref = experiment("dataset-research", "dataset_research", method_kind="classical_cv")
    base_ref = experiment(
        "baseline-comparison",
        "baseline_comparison",
        method_kind="classical_cv",
        compared_methods=list(method_refs.values())[:3],
        parents=[data_ref],
        benchmarks=[baseline_ref],
        training_required=True,
        selection_rationale="Validation retrieval WER remained at least 96.93%; a bounded fixed ridge fit diagnoses whether this representation can generalize. No pretrained weights were available in the authorized local inputs.",
    )
    train_ref = experiment(
        "training",
        "training",
        method_kind="custom_training",
        parents=[base_ref],
        benchmarks=[training_ref],
        training_required=True,
        recipe_ref=method_refs["ridge"],
        selected_model_ref=model,
        selection_rationale="Fixed 512-feature ridge, lambda 10, train-only word vocabulary and centering; supervised fit follows the saved validation baseline. No test-driven tuning or pretrained model download.",
    )
    experiment(
        "heldout-evaluation",
        "heldout_evaluation",
        method_kind="custom_training",
        parents=[base_ref, train_ref],
        benchmarks=[heldout_ref, style_ref, glyph_ref],
        training_required=True,
        selected_model_ref=model,
        selection_rationale="Report all methods, OOV errors, writer breakdowns, weak style evidence and limited authentic reuse. No method is qualified for integration.",
    )
    lineage = {
        "result_sha256": "sha256:" + file_hash(output / "result.json"),
        "model": model,
        "source_snapshot": artifacts[-1],
        "benchmark_ids": [v.record_id for v in benchmarks.values()],
        "experiment_ids": [v.record_id for v in experiments.values()],
        "record_import_executes_training": False,
    }
    (output / "lineage.json").write_text(json.dumps(lineage, indent=2) + "\n")
    print(json.dumps(audit(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
