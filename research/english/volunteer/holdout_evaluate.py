"""Frozen, owner-only paired evaluation; preparation imports no model framework."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from research.english.r6_contract import tensor_header
from research.english.volunteer import evaluate as evaluation
from research.english.volunteer import frozen, holdout
from research.english.volunteer.holdout import _atomic_write, _json_bytes, _read_json
from research.pretrained.artifacts import digest_file

PLAN_NAME = "evaluation-plan.json"
CLAIM_NAME = "evaluation-claim.json"
REPORT_NAME = "evaluation-report.json"
DEFAULT_ADAPTATION_RUN = (
    frozen.FROZEN_RUN.parent / "r6-volunteer-adaptation-20261023-capture-v2-ready"
)


def verify_candidate(run: Path, baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the completed, selected, optimizer-free candidate and R6 ancestry."""
    root = Path(run).resolve()
    report_path = root / "adaptation-report.json"
    report = _read_json(report_path, "adaptation_report")
    selection = report.get("selection", {})
    chosen = selection.get("selected_pass")
    if (
        report.get("version") != "penforge-volunteer-adaptation-report-v1"
        or report.get("status") != "training_complete_candidate_selected"
        or report.get("product_qualification") != "none"
        or report.get("test_evaluated") is not False
        or selection.get("status") != "candidate_selected_on_validation"
        or type(chosen) is not int
        or chosen not in (1, 2)
        or selection.get("forgetting_floor_correct") != 1348
        or selection.get("selected_validation_correct", -1) < 1348
        or report.get("development_writers") != ["W01", "W02", "W03", "W04", "W05"]
        or report.get("data", {}).get("frozen_winner", {}).get("checkpoint_sha256")
        != baseline["checkpoint_sha256"]
    ):
        raise ValueError("volunteer_holdout_adapted_candidate_not_eligible_or_wrong_ancestry")
    checkpoint = root / "training/best"
    if checkpoint.is_symlink() or not checkpoint.is_dir():
        raise ValueError("volunteer_holdout_adapted_checkpoint_missing_or_unsafe")
    if sorted(item.name for item in checkpoint.iterdir()) != ["state.json", "state.safetensors"]:
        raise ValueError("volunteer_holdout_adapted_checkpoint_inventory_changed")
    state = _read_json(checkpoint / "state.json", "adapted_state")
    weights = checkpoint / "state.safetensors"
    if weights.is_symlink() or not weights.is_file():
        raise ValueError("volunteer_holdout_adapted_weights_missing_or_unsafe")
    weights_hash = digest_file(weights)
    inventory = baseline["initialization"]["trainable_inventory"]
    if (
        state.get("status") != "pass_complete"
        or state.get("weights_only") is not True
        or state.get("pass_index") != chosen
        or state.get("candidate") != report.get("candidate")
        or state.get("tensors_sha256") != weights_hash
        or selection.get("selected_checkpoint") != weights_hash
        or state.get("trainable_inventory") != inventory
        or state.get("source_state_sha256") != selection.get("selected_source_checkpoint")
    ):
        raise ValueError("volunteer_holdout_adapted_state_disagrees_with_selection")
    header = tensor_header(weights)
    if set(header) != {f"model.{name}" for name in inventory}:
        raise ValueError("volunteer_holdout_adapted_tensor_inventory_changed")
    for name, record in inventory.items():
        tensor = header[f"model.{name}"]
        if tensor["shape"] != record["shape"] or frozen._normalized_dtype(
            tensor["dtype"]
        ) != frozen._normalized_dtype(record["dtype"]):
            raise ValueError("volunteer_holdout_adapted_tensor_shape_or_dtype_changed")
    pilot = report.get("data", {}).get("pilot", {})
    if not isinstance(pilot, Mapping) or not isinstance(pilot.get("run"), str):
        raise ValueError("volunteer_holdout_development_source_lineage_missing")
    pilot_root = Path(pilot["run"])
    pilot_report = _read_json(pilot_root / "report.json", "development_report")
    if digest_file(pilot_root / "report.json") != pilot.get("report_sha256") or digest_file(
        pilot_root / "crop-manifest.json"
    ) != pilot.get("crop_manifest_sha256"):
        raise ValueError("volunteer_holdout_development_source_lineage_changed")
    development_sources = pilot_report.get("data", {}).get("source_images", [])
    if not isinstance(development_sources, list) or [
        source.get("writer_id") for source in development_sources
    ] != ["W01", "W02", "W03", "W04", "W05"]:
        raise ValueError("volunteer_holdout_development_writer_inventory_changed")
    return {
        "run": str(root),
        "candidate": report["candidate"],
        "pass_index": chosen,
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": weights_hash,
        "state_sha256": digest_file(checkpoint / "state.json"),
        "report_sha256": digest_file(report_path),
        "baseline_checkpoint_sha256": baseline["checkpoint_sha256"],
        "development_source_sha256": [
            holdout._digest(source.get("source_sha256"), "development_source_sha256")
            for source in development_sources
        ],
    }


def prepare_plan(
    run: Path,
    *,
    frozen_run: Path = frozen.FROZEN_RUN,
    adaptation_run: Path = DEFAULT_ADAPTATION_RUN,
    frozen_verifier: Callable[..., dict[str, Any]] = frozen.verify_frozen_winner,
    candidate_verifier: Callable[..., dict[str, Any]] = verify_candidate,
) -> dict[str, Any]:
    """Verify data/models and pin the pair before inference; idempotent, data only."""
    root, prepared, manifest = holdout.load_prepared(run)
    baseline = frozen_verifier(frozen_run)
    candidate = candidate_verifier(adaptation_run, baseline)
    if baseline["inference"] != frozen.INFERENCE:
        raise ValueError("volunteer_holdout_inference_recipe_changed")
    # Reject copied development pages as well as overlapping writer roles.
    seen = set(candidate["development_source_sha256"])
    if seen & {item["source_sha256"] for item in manifest["sources"]}:
        raise ValueError("volunteer_holdout_source_overlaps_training")
    plan = {
        "version": "penforge-volunteer-holdout-evaluation-plan-v1",
        "status": "frozen_pair_awaiting_owner_evaluation",
        "crop_manifest_sha256": prepared["crop_manifest_sha256"],
        "roles_sha256": prepared["roles_sha256"],
        "included_samples": len(manifest["samples"]),
        "excluded_cases": len(manifest.get("excluded", [])),
        "writers": list(holdout.HOLDOUT_WRITERS),
        "baseline": baseline,
        "adapted": candidate,
        "inference": dict(frozen.INFERENCE),
        "implementation_sha256": {
            name: digest_file(Path(__file__).parent / name)
            for name in (
                "holdout.py",
                "holdout_evaluate.py",
                "evaluate.py",
                "frozen.py",
                "prepare.py",
            )
        },
        "model_execution": False,
        "training_performed": False,
    }
    holdout._write_new_or_identical(root, PLAN_NAME, _json_bytes(plan))
    return plan


def _apply_candidate(torch: Any, model: Any, candidate: Mapping[str, Any]) -> None:
    from research.english.r6_model import initialization

    checkpoint = Path(candidate["checkpoint_path"])
    weights = checkpoint / "state.safetensors"
    if (
        digest_file(weights) != candidate["checkpoint_sha256"]
        or digest_file(checkpoint / "state.json") != candidate["state_sha256"]
    ):
        raise ValueError("volunteer_holdout_adapted_weights_changed_before_application")
    state = _read_json(checkpoint / "state.json", "adapted_state")
    tensors = initialization.read_delta_tensors(weights)
    initialization.apply_delta(torch, model, tensors, state["trainable_inventory"])


def run(
    run: Path,
    *,
    owner_run: bool = False,
    frozen_run: Path = frozen.FROZEN_RUN,
    adaptation_run: Path = DEFAULT_ADAPTATION_RUN,
    plan_preparer: Callable[..., dict[str, Any]] = prepare_plan,
    torch_factory: Callable[[int], Any] = evaluation._prepare_torch,
    model_loader: Callable[
        ..., tuple[Any, Any, Any, dict[str, Any]]
    ] = evaluation._load_selected_model,
    candidate_applier: Callable[..., None] = _apply_candidate,
    generator: Callable[
        ..., tuple[list[dict[str, Any]], dict[str, Any]]
    ] = evaluation.generate_records,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Evaluate both frozen states once; a claim refuses retries and partial overwrites."""
    if not owner_run:
        raise ValueError("volunteer_holdout_evaluate_requires_owner_run")
    root, _, manifest = holdout.load_prepared(run)
    outputs = [CLAIM_NAME, REPORT_NAME, holdout.COMPARISON_NAME]
    outputs += [f"{label}-predictions-private.jsonl" for label in ("baseline", "adapted")]
    if any((root / name).exists() or (root / name).is_symlink() for name in outputs):
        raise FileExistsError("volunteer_holdout_evaluation_already_claimed_no_automatic_retry")
    if not (root / PLAN_NAME).is_file():
        raise ValueError("volunteer_holdout_verify_and_freeze_pair_before_evaluation")
    plan = plan_preparer(root, frozen_run=frozen_run, adaptation_run=adaptation_run)
    claim = {
        "version": "penforge-volunteer-holdout-evaluation-claim-v1",
        "status": "in_progress",
        "evaluation_plan_sha256": digest_file(root / PLAN_NAME),
        "automatic_retry": False,
    }
    with (root / CLAIM_NAME).open("xb") as stream:
        os.chmod(root / CLAIM_NAME, 0o600)
        stream.write(_json_bytes(claim))
    started = time.monotonic()
    try:
        torch = torch_factory(int(plan["inference"]["seed"]))
        model, tokenizer, processor, _ = model_loader(plan["baseline"], torch)
        samples = [holdout._manifest_view(sample) for sample in manifest["samples"]]
        timing = {}
        for label in ("baseline", "adapted"):
            if label == "adapted":
                candidate_applier(torch, model, plan["adapted"])
            torch.manual_seed(int(plan["inference"]["seed"]))

            def emit(line: str, model_label: str = label) -> None:
                if progress is not None:
                    value = json.loads(line)
                    value.update({"stage": "holdout-evaluate", "model": model_label})
                    progress(json.dumps(value, sort_keys=True))

            records, stats = generator(
                torch,
                model,
                tokenizer,
                processor,
                samples,
                root,
                batch_size=int(plan["inference"]["batch_size"]),
                max_new_tokens=int(plan["inference"]["max_new_tokens"]),
                progress=emit,
            )
            # Score before publishing to reject missing IDs, modified truths or writers.
            predictions = {record["sample_id"]: record for record in records}
            if len(records) != len(samples) or len(predictions) != len(records):
                raise ValueError("volunteer_holdout_generated_inventory_changed")
            holdout.score(manifest, predictions)
            payload = "".join(
                json.dumps(
                    {
                        "version": holdout.PREDICTION_VERSION,
                        **record,
                        "raw_prediction": record["prediction"],
                    },
                    sort_keys=True,
                )
                + "\n"
                for record in records
            ).encode()
            target = root / f"{label}-predictions-private.jsonl"
            holdout._write_new_or_identical(root, target.name, payload)
            timing[label] = stats
        comparison = holdout.compare(
            root,
            baseline_predictions=root / "baseline-predictions-private.jsonl",
            adapted_predictions=root / "adapted-predictions-private.jsonl",
        )
        report = {
            "version": "penforge-volunteer-holdout-evaluation-report-v1",
            **holdout.summary(comparison),
            "status": "paired_external_evaluation_complete",
            "model_execution": True,
            "training_performed": False,
            "evaluation_plan_sha256": claim["evaluation_plan_sha256"],
            "comparison_sha256": digest_file(root / holdout.COMPARISON_NAME),
            "baseline_checkpoint_sha256": plan["baseline"]["checkpoint_sha256"],
            "adapted_checkpoint_sha256": plan["adapted"]["checkpoint_sha256"],
            "timing": timing,
            "wall_seconds": time.monotonic() - started,
        }
        _atomic_write(root / REPORT_NAME, _json_bytes(report))
    except BaseException:
        claim["status"] = "stopped_preserve_outputs_no_automatic_retry"
        _atomic_write(root / CLAIM_NAME, _json_bytes(claim))
        raise
    claim["status"] = "complete"
    _atomic_write(root / CLAIM_NAME, _json_bytes(claim))
    return report
