"""Reuse the unchanged R5 validation control only under complete pinned equivalence."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from research.english.r5_data.common import (
    canonical_hash,
    checked,
    digest_file,
    read_jsonl,
    write_json,
)
from research.english.r5_eval.lifecycle import verify_frozen
from research.english.r6_contract import R5Initialization
from research.english.r6_eval.scoring import score_predictions
from research.pretrained.artifacts import ROOT, verify_bundle


def reuse_control(r5_run: Path, stage: Path, bundle: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("r6_control_output_must_be_new")
    session = json.loads(checked(r5_run, "session.json").read_text())
    frozen = verify_frozen(checked(r5_run, "winner-frozen.json"), session)
    init = R5Initialization(
        Path(session["initialization_adapter"]["derived_path"]),
        Path(frozen["winner"]["checkpoint_path"]),
        r5_run / "winner-frozen.json",
    )
    initial = init.verify()
    if verify_bundle(bundle) != session["model_lineage"]["bundle"]:
        raise ValueError("r6_control_bundle_environment_equivalence_failed")
    snapshot = json.loads(checked(r5_run, "execution-source.json").read_text())
    # Pin the complete original evaluator chain and objective/inventory adapters. R6's
    # scorer delegates to these unchanged files; none is reimplemented approximately.
    names = [
        name
        for name in snapshot["files"]
        if name.startswith(
            ("research/english/r5_model/", "research/pretrained/", "research/evaluation/")
        )
        and name.endswith(".py")
    ]
    names += ["research/english/r5_contract.py", "research/english/r5_eval/scoring.py"]
    for name in names:
        if digest_file(checked(ROOT, name)) != snapshot["files"][name]:
            raise ValueError("r6_control_frozen_evaluator_dependency_changed")
    from importlib.metadata import version

    libraries = {name: version(name) for name in session["model_lineage"]["libraries"]}
    if libraries != session["model_lineage"]["libraries"]:
        raise ValueError("r6_control_environment_changed")
    source_stage = Path(session["model_lineage"]["initialization"]["path"])
    del source_stage  # Base path is verified above, never used as an image source.
    prep = json.loads(
        checked(
            Path(json.loads(checked(stage, "r6-stage.json").read_text())["source"]),
            "preparation.json",
        ).read_text()
    )
    if digest_file(checked(stage, "validation.jsonl")) != prep["files"]["validation.jsonl"]:
        raise ValueError("r6_control_validation_mapping_not_identical")
    rows = read_jsonl(checked(stage, "validation.jsonl"))
    for row in rows:
        if digest_file(checked(stage, row["path"])) != row["hash"]:
            raise ValueError("r6_control_pixel_inputs_changed")
    report = json.loads(checked(r5_run, "training/training-report.json").read_text())
    result = report["selection"]["winner"]
    if report["status"] != "training_complete_validation_selected" or result["pass_index"] != 3:
        raise ValueError("r6_control_not_completed_frozen_r5_pass_three")
    prediction = checked(r5_run, "training/pass-0003-validation-predictions-private.jsonl")
    if digest_file(prediction) != result["predictions_sha256"]:
        raise ValueError("r6_control_prediction_hash_changed")
    score = score_predictions(rows, read_jsonl(prediction))
    if any(score[key] != result[key] for key in ("n", "correct", "accuracy", "scalar_cer")):
        raise ValueError("r6_control_authoritative_score_disagrees")
    recipe = session["model_lineage"]["recipe"]
    if (
        recipe["seed"],
        recipe["max_label_tokens"],
        recipe["decoding"],
        recipe["validation_preprocessing"],
    ) != (
        20261023,
        8,
        "greedy_no_lexicon_no_language_correction",
        "pinned_upstream_vit_rgb_resize_384_v1",
    ):
        raise ValueError("r6_control_decoding_or_preprocessing_changed")
    output.mkdir(mode=0o700)
    target = output / "validation-predictions-private.jsonl"
    shutil.copyfile(prediction, target)
    target.chmod(0o600)
    value = {
        "version": "r6-unchanged-r5-control-v1",
        "status": "equivalent_saved_validation_reused",
        "name": "r5_unchanged_control",
        "kind": "r5_trainable_delta",
        "validation_metrics": score,
        "per_crop_ms_p50": result["per_crop_ms_p50"],
        "checkpoint_path": str(init.delta_checkpoint),
        "checkpoint_files": initial["delta_files"],
        "predictions_path": str(target),
        "predictions_sha256": digest_file(target),
        "inference_recipe": {
            "preprocessing": recipe["validation_preprocessing"],
            "decoding": recipe["decoding"],
            "seed": recipe["seed"],
            "max_new_tokens": recipe["max_label_tokens"],
            "batch_size": 8,
            "order_namespace": "pass-0003",
        },
        "equivalence": {
            "initialization": initial,
            "evaluator_sources": {name: snapshot["files"][name] for name in names},
            "libraries": libraries,
            "validation_mapping_sha256": digest_file(stage / "validation.jsonl"),
            "r5_report_sha256": digest_file(r5_run / "training/training-report.json"),
            "r5_frozen_winner_sha256": digest_file(r5_run / "winner-frozen.json"),
            "r5_prediction_sha256": digest_file(prediction),
            "authoritative_scorer": "unchanged_r5_complete_unique_sample_id_join_v1",
        },
        "inference_repeated": False,
        "test_evaluated": False,
        "model_execution": False,
        "latency_basis": "historical_r5_batch_amortized_median_not_remeasured_r6",
    }
    value["equivalence_sha256"] = canonical_hash(value["equivalence"])
    write_json(output / "report.json", value)
    return value
