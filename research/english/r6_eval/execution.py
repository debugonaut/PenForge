"""R6 lifecycle integration. Imports remain inert; model execution is owner-only."""

from __future__ import annotations

import importlib
import importlib.abc
import inspect
import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from research.english.r5_data.common import (
    canonical_hash,
    checked,
    digest_file,
    read_jsonl,
    write_json,
)
from research.english.r5_eval.lifecycle import file_inventory
from research.english.r6_contract import AggregateLimits, R5Initialization, rank_key, tensor_header
from research.english.r6_data.prepare import check
from research.english.r6_eval.control import reuse_control
from research.english.r6_eval.lifecycle import freeze
from research.english.r6_eval.scoring import paired_comparison, score_predictions
from research.pretrained.artifacts import ROOT, verify_bundle


class NoModelImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"torch", "transformers"}:
            raise ValueError("r6_metadata_preflight_must_not_import_model_frameworks")
        return None


@contextmanager
def metadata_only():
    guard = NoModelImports()
    sys.meta_path.insert(0, guard)
    try:
        yield
    finally:
        sys.meta_path.remove(guard)


def progress(stage, **counts):
    print(json.dumps({"stage": stage, **counts}, sort_keys=True), flush=True)


def private_run(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("r6_run_root_must_not_be_symlink")
    path = path.resolve()
    origin = Path(os.environ.get("PENFORGE_R6_ORIGIN_ROOT", str(ROOT))).resolve()
    private = origin / "research/runs"
    if path == private or (path.is_relative_to(origin) and not path.is_relative_to(private)):
        raise ValueError("r6_run_requires_private_new_directory")
    return path


def data_context(stage: Path, bundle: Path, r5_run: Path, run: Path) -> dict:
    paths = [stage, bundle, r5_run, run]
    if any(p.is_symlink() for p in paths):
        raise ValueError("r6_symlink_input_root")
    stage, bundle, r5_run = map(lambda p: p.resolve(), (stage, bundle, r5_run))
    run = private_run(run)
    for protected in (stage, bundle, r5_run):
        if run.is_relative_to(protected) or protected.is_relative_to(run):
            raise ValueError("r6_run_must_be_separate_from_inputs_and_r5")
    progress("r6-verifying-data-and-initialization", model_execution=False)
    data = check(stage)
    session = json.loads(checked(r5_run, "session.json").read_text())
    winner = json.loads(checked(r5_run, "winner-frozen.json").read_text())
    initialization = R5Initialization(
        Path(session["initialization_adapter"]["derived_path"]),
        Path(winner["winner"]["checkpoint_path"]),
        r5_run / "winner-frozen.json",
    )
    initial = initialization.verify()
    bundle_receipt = verify_bundle(bundle)
    if (
        data["receipt"]["preparation_sha256"] != session["data_preparation_sha256"]
        or bundle_receipt != session["model_lineage"]["bundle"]
    ):
        raise ValueError("r6_data_or_bundle_differs_from_r5_initialization_lineage")
    if [len(data["rows"][key]) for key in ("train", "validation")] != [6656, 1664]:
        raise ValueError("r6_controlled_study_inventory_changed")
    return {
        "stage": stage,
        "bundle": bundle,
        "r5_run": r5_run,
        "run": run,
        "data": data,
        "initialization": initialization,
        "initialization_metadata": initial,
        "bundle_receipt": bundle_receipt,
    }


def part_b_preflight(ctx) -> dict:
    handoff = ROOT / "research/ENGLISH_R6_MODEL.md"
    package = ROOT / "research/english/r6_model/__init__.py"
    if not handoff.is_file() or not package.is_file():
        raise ValueError("r6_part_b_package_or_documented_handoff_incomplete")
    with metadata_only():
        module = importlib.import_module("research.english.r6_model")
        if not all(
            callable(getattr(module, key, None))
            for key in ("load_plan", "preflight", "run_bounded_training")
        ):
            raise ValueError("r6_part_b_public_interface_incomplete")
        configs = sorted((ROOT / "research/configs").glob("english-r6-model*.json"))
        if len(configs) != 1:
            raise ValueError("r6_explicit_single_bounded_candidate_recipe_required")
        recipe_path, resource_receipt = effective_recipe(ctx, configs[0])
        init = ctx["initialization"]
        kwargs = {
            "staging": ctx["stage"],
            "mapping_name": "train-validation.jsonl",
            "model_bundle": ctx["bundle"],
            "init_checkpoint": init.base_checkpoint,
            "init_delta": init.delta_checkpoint,
            "r5_winner": init.winner_file,
            "output": ctx["run"] / "training",
            "recipe_path": recipe_path,
        }
        parameters = inspect.signature(module.load_plan).parameters
        if not set(kwargs) <= set(parameters):
            raise ValueError("r6_part_b_load_plan_contract_incompatible")
        plan = module.load_plan(**kwargs)
        prepared = module.preflight(plan, scorer=ctx["data"]["inputs"].score_validation)
    recipe = plan.recipe
    if (
        recipe.pass_limit > 3
        or recipe.max_run_wall_seconds > 16200
        or recipe.max_cpu_seconds > 32400
        or recipe.max_rss_mb > 6000
        or recipe.max_output_bytes > 3_000_000_000
    ):
        raise ValueError("r6_model_recipe_exceeds_shared_aggregate_envelope")
    from research.english.r6_model.budgets import directory_bytes

    overhead = directory_bytes(ctx["run"]) - directory_bytes(ctx["run"] / "training")
    if resource_receipt["conservative_publication_peak_bytes"] + overhead > recipe.max_output_bytes:
        raise ValueError("r6_whole_run_checkpoint_publication_peak_exceeds_ceiling")
    return {
        "module": module,
        "plan": plan,
        "prepared": prepared,
        "recipe_path": recipe_path,
        "handoff_sha256": digest_file(handoff),
        "resource_integration": resource_receipt,
    }


def effective_recipe(ctx, source):
    """Pin a resource-only adjustment for the inspected publication order, if needed."""
    from research.english.r6_model.checkpoint import (
        estimate_full_state_bytes,
        estimate_selected_state_bytes,
    )

    recipe = json.loads(source.read_text())
    inventory = ctx["initialization_metadata"]["trainable_inventory"]
    # Candidate is written before full-state commit. Count both retained selected
    # states as well as both full states; do not rely on the smaller model estimate.
    full = estimate_full_state_bytes(inventory)
    selected = estimate_selected_state_bytes(inventory)
    peak = 2 * full + 2 * selected
    if peak + 10_000_000 > 3_000_000_000:
        raise ValueError("r6_actual_publication_order_exceeds_owner_output_ceiling")
    effective = dict(recipe)
    if peak + 10_000_000 > recipe["max_output_bytes"]:
        effective["max_output_bytes"] = 3_000_000_000
    receipt = {
        "policy": "resource_only_publication_peak_adjustment_within_existing_owner_ceiling",
        "source_recipe_sha256": digest_file(source),
        "native_output_bytes": recipe["max_output_bytes"],
        "effective_output_bytes": effective["max_output_bytes"],
        "conservative_publication_peak_bytes": peak,
        "layout": "latest_plus_latest.new_plus_best_plus_selected-candidate",
        "model_hyperparameters_changed": False,
        "measured_runtime": False,
    }
    run = ctx["run"]
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    allowed = {"execution-recipe.json", "resource-integration.json"}
    if not (run / "session.json").exists() and set(p.name for p in run.iterdir()) - allowed:
        raise ValueError("r6_preflight_run_directory_collision")
    for name, value in (
        ("execution-recipe.json", effective),
        ("resource-integration.json", receipt),
    ):
        path = run / name
        if path.exists():
            if json.loads(checked(run, name).read_text()) != value:
                raise ValueError("r6_effective_recipe_or_resource_receipt_drift")
        else:
            write_json(path, value)
    return run / "execution-recipe.json", receipt


def lineage(ctx, model) -> dict:
    return {
        "version": "r6-session-v1",
        "data_receipt_sha256": ctx["data"]["receipt_sha256"],
        "mapping_hash": ctx["data"]["mapping_hash"],
        "initialization": ctx["initialization_metadata"],
        "bundle": ctx["bundle_receipt"],
        "model_lineage_hash": model["prepared"].lineage_hash,
        "model_lineage": model["prepared"].lineage,
        "recipe_sha256": digest_file(model["recipe_path"]),
        "part_b_handoff_sha256": model["handoff_sha256"],
        "resource_integration": model["resource_integration"],
        "scorer_sources": {
            name: digest_file(checked(ROOT, name))
            for name in (
                "research/english/r6_eval/scoring.py",
                "research/english/r5_eval/scoring.py",
                "research/evaluation/harness.py",
            )
        },
        "aggregate_limits": AggregateLimits().__dict__,
        "integration_reserve": {
            "wall_seconds": 600,
            "cpu_seconds": 600,
            "policy": "reserve from envelope for source/preflight/control/selection; native tighter limits retained",
        },
        "final_policy": "unavailable_no_eligible_untouched_inventory_no_final_execution",
    }


def preflight(ctx) -> dict:
    model = part_b_preflight(ctx)
    value = lineage(ctx, model)
    run = ctx["run"]
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = run / "session.json"
    if path.exists():
        if json.loads(checked(run, "session.json").read_text()) != value:
            raise ValueError("r6_session_source_data_environment_or_recipe_changed")
    else:
        if set(p.name for p in run.iterdir()) - {
            "execution-recipe.json",
            "resource-integration.json",
        }:
            raise ValueError("r6_preflight_run_directory_collision")
        write_json(path, value)
    return {
        "model": model,
        "lineage": value,
        "status": "r6_integrated_metadata_preflight_passed",
        "model_execution": False,
    }


def require_execution(owner_run):
    if owner_run is not True:
        raise ValueError("r6_model_execution_requires_owner_run")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")


def budget_factory(ctx, *, prior_wall=0.0, prior_cpu=0.0):
    """One package/candidate; count the entire run and reserve integration overhead."""
    from research.english.r6_model.budgets import Consumption, RunBudget

    def factory(plan, output):
        limited = replace(
            plan,
            run_wall_seconds=min(plan.run_wall_seconds, 15_600),
            cpu_seconds=min(plan.cpu_seconds, 31_800),
            output_bytes=min(plan.output_bytes, 3_000_000_000),
            prior=Consumption(
                wall_seconds=max(plan.prior.wall_seconds, prior_wall),
                cpu_seconds=max(plan.prior.cpu_seconds, prior_cpu),
            ),
        )
        return RunBudget(limited, ctx["run"])

    return factory


def control(ctx):
    if (ctx["run"] / "training").exists() or (ctx["run"] / "winner-frozen.json").exists():
        raise ValueError("r6_control_must_precede_training_and_freeze")
    progress("r6-rescoring-equivalent-saved-control", samples=1664, model_execution=False)
    return reuse_control(ctx["r5_run"], ctx["stage"], ctx["bundle"], ctx["run"] / "control")


def train(ctx, model, *, owner_run=False, resume=False):
    require_execution(owner_run)
    run = ctx["run"]
    verified_control(ctx)
    if (run / "winner-frozen.json").exists():
        raise ValueError("r6_training_forbidden_after_selection_freeze")
    report_path = run / "training/training-report.json"
    if (
        report_path.exists()
        and json.loads(report_path.read_text()).get("status")
        == "training_complete_validation_selected"
    ):
        raise ValueError("r6_completed_training_must_not_be_repeated")
    attempts = run / "attempts"
    attempts.mkdir(exist_ok=True, mode=0o700)
    previous = sorted(attempts.glob("attempt-*.json"))
    if bool(previous) != bool(resume):
        raise ValueError("r6_interrupted_attempt_requires_explicit_resume")
    if model["plan"].recipe.pass_limit + len(previous) > AggregateLimits().total_passes:
        raise ValueError("r6_aggregate_pass_and_replay_allowance_exhausted")
    prior_wall, prior_cpu = previous_consumption(
        attempts, previous, expected_session=digest_file(run / "session.json")
    )
    if (
        prior_wall >= model["plan"].recipe.max_run_wall_seconds
        or prior_cpu >= model["plan"].recipe.max_cpu_seconds
    ):
        raise ValueError("r6_aggregate_attempt_budget_exhausted")
    number = len(previous) + 1
    write_json(
        attempts / f"attempt-{number:04d}.json",
        {
            "status": "claimed_before_model_load",
            "resume": resume,
            "session_sha256": digest_file(run / "session.json"),
        },
    )
    progress(
        "r6-starting-owner-training", training_samples=6656, validation_samples=1664, resume=resume
    )
    wall_started, cpu_started = time.monotonic(), time.process_time()
    try:
        value = model["module"].run_bounded_training(
            model["plan"],
            ctx["data"]["inputs"].score_validation,
            owner_run=True,
            resume=resume,
            budget_factory=budget_factory(ctx, prior_wall=prior_wall, prior_cpu=prior_cpu),
        )
    except BaseException as error:
        write_json(
            attempts / f"outcome-{number:04d}.json",
            {
                "status": "incomplete_claim_retained",
                "exception": type(error).__name__,
                "automatic_retry": False,
                "wall_seconds": time.monotonic() - wall_started,
                "cpu_seconds": time.process_time() - cpu_started,
            },
        )
        raise
    write_json(
        attempts / f"outcome-{number:04d}.json",
        {
            "status": value["status"],
            "test_evaluated": False,
            "wall_seconds": time.monotonic() - wall_started,
            "cpu_seconds": time.process_time() - cpu_started,
        },
    )
    return value


def verified_control(ctx):
    run = ctx["run"]
    path = checked(run, "control/report.json")
    value = json.loads(path.read_text())
    predictions = checked(run, "control/validation-predictions-private.jsonl")
    score = score_predictions(ctx["data"]["rows"]["validation"], read_jsonl(predictions))
    equivalence = value.get("equivalence", {})
    original = checked(ctx["r5_run"], "training/training-report.json")
    original_report = json.loads(original.read_text())
    if (
        value.get("status") != "equivalent_saved_validation_reused"
        or value.get("name") != "r5_unchanged_control"
        or value.get("equivalence_sha256") != canonical_hash(equivalence)
        or equivalence.get("r5_report_sha256") != digest_file(original)
        or value.get("predictions_sha256") != digest_file(predictions)
        or value.get("predictions_sha256")
        != original_report["selection"]["winner"]["predictions_sha256"]
        or value.get("per_crop_ms_p50") != original_report["selection"]["winner"]["per_crop_ms_p50"]
        or value.get("validation_metrics") != score
        or value.get("checkpoint_files") != ctx["initialization_metadata"]["delta_files"]
    ):
        raise ValueError("r6_reused_control_evidence_or_equivalence_changed")
    return value


def previous_consumption(attempts, claims, *, expected_session=None):
    """Unmeasured hard interruption cannot reset the aggregate budget."""
    import math

    wall, cpu = 0.0, 0.0
    for claim in claims:
        if (
            expected_session is not None
            and json.loads(claim.read_text()).get("session_sha256") != expected_session
        ):
            raise ValueError("r6_attempt_claim_session_drift")
        number = claim.stem.removeprefix("attempt-")
        path = attempts / f"outcome-{number}.json"
        if not path.is_file() or path.is_symlink():
            raise ValueError("r6_unmeasured_interrupted_attempt_resume_refused_budget_not_reset")
        value = json.loads(path.read_text())
        for key in ("wall_seconds", "cpu_seconds"):
            n = value.get(key)
            if (
                isinstance(n, bool)
                or not isinstance(n, (int, float))
                or not math.isfinite(n)
                or n < 0
            ):
                raise ValueError("r6_attempt_consumption_missing_or_invalid")
        wall += value["wall_seconds"]
        cpu += value["cpu_seconds"]
    return wall, cpu


def freeze_selection(ctx, prepared):
    """Independently rescore every candidate, retain only actual verified checkpoints."""
    run = ctx["run"]
    control_path = checked(run, "control/report.json")
    baseline = verified_control(ctx)
    rows = ctx["data"]["rows"]["validation"]
    baseline_predictions = checked(run, "control/validation-predictions-private.jsonl")
    baseline_records = read_jsonl(baseline_predictions)
    baseline_score = score_predictions(rows, baseline_records)
    if (
        digest_file(baseline_predictions) != baseline["predictions_sha256"]
        or baseline_score != baseline["validation_metrics"]
    ):
        raise ValueError("r6_control_predictions_or_metrics_drift")
    report_path = checked(run, "training/training-report.json")
    report = json.loads(report_path.read_text())
    if (
        report.get("status") != "training_complete_validation_selected"
        or report.get("test_evaluated") is not False
        or report.get("lineage_hash") != prepared.lineage_hash
    ):
        raise ValueError("r6_training_incomplete_or_wrong_lineage")
    results = report.get("pass_results", [])
    if not results or len(results) > 3:
        raise ValueError("r6_complete_bounded_candidate_evidence_required")
    scored = []
    evidence = [control_path, baseline_predictions, report_path]
    analyses = []
    if [r.get("pass_index") for r in results] != list(range(1, len(results) + 1)):
        raise ValueError("r6_candidate_passes_not_contiguous")
    for result in results:
        index = result["pass_index"]
        if result.get("samples") != 6656 or result.get("optimizer_steps") != 1664:
            raise ValueError("r6_committed_complete_pass_coverage_missing")
        path = checked(run, f"training/pass-{index:04d}-validation-predictions-private.jsonl")
        records = read_jsonl(path)
        metrics = score_predictions(rows, records)
        if digest_file(path) != result["predictions_sha256"] or any(
            metrics[key] != result[key] for key in ("correct", "n", "accuracy", "scalar_cer")
        ):
            raise ValueError("r6_candidate_raw_id_join_or_hash_disagrees")
        item = {
            "name": f"{report['candidate']}_pass_{index:04d}",
            "validation_metrics": metrics,
            "per_crop_ms_p50": result["per_crop_ms_p50"],
            "pass_index": index,
            "predictions_path": str(path),
            "predictions_sha256": digest_file(path),
            "checkpoint_sha256": result["checkpoint_sha256"],
        }
        rank_key(item)
        scored.append(item)
        analyses.append(
            {"name": item["name"], "comparison": paired_comparison(rows, baseline_records, records)}
        )
        evidence.append(path)
    best = min(scored, key=rank_key)
    retained = report.get("retained_candidate", {})
    if (
        retained.get("name") != best["name"]
        or retained.get("complete_pass") is not True
        or retained.get("samples") != 6656
        or retained.get("predictions_sha256") != best["predictions_sha256"]
    ):
        raise ValueError("r6_retained_candidate_does_not_match_authoritative_selection")
    state_path = checked(run, "training/best/state.json")
    state = json.loads(state_path.read_text())
    if (
        state.get("status") != "pass_complete"
        or state.get("weights_only") is not True
        or state.get("pass_index") != best["pass_index"]
        or state.get("lineage_hash") != prepared.lineage_hash
        or state.get("tensors_sha256") != best["checkpoint_sha256"]
        or digest_file(checked(run, "training/best/state.safetensors")) != best["checkpoint_sha256"]
    ):
        raise ValueError("r6_selected_checkpoint_does_not_match_complete_validation_winner")
    expected_inventory = ctx["initialization_metadata"]["trainable_inventory"]
    header = tensor_header(checked(run, "training/best/state.safetensors"))
    if (
        state.get("version") != "english-r6-selected-state-v2"
        or state.get("trainable_inventory") != expected_inventory
        or set(header) != {"model." + name for name in expected_inventory}
        or any(
            header["model." + name]["shape"] != item["shape"]
            or header["model." + name]["dtype"] != "F32"
            for name, item in expected_inventory.items()
        )
    ):
        raise ValueError("r6_selected_tensor_inventory_drift")
    passes = report.get("passes", [])
    # A resumed report contains detailed observations for this invocation only.
    # The hash-pinned PassCommit list retains every earlier complete pass.
    if any(
        p.get("status") != "complete_one_pass"
        or p.get("samples") != 6656
        or p.get("unique_samples") != 6656
        for p in passes
    ):
        raise ValueError("r6_truncated_training_pass_not_eligible")
    if report.get("training", {}).get("passes_completed") != len(results):
        raise ValueError("r6_completed_pass_count_disagrees_with_commits")
    if retained.get("checkpoint_files") != file_inventory(run / "training/best"):
        raise ValueError("r6_retained_checkpoint_inventory_changed")
    adapted = {
        **best,
        "kind": "r6_trainable_delta",
        "checkpoint_path": str(run / "training/best"),
        "checkpoint_files": file_inventory(run / "training/best"),
        "inference_recipe": {
            **retained["inference"],
            "seed": prepared.recipe.seed,
            "order_namespace": f"pass-{best['pass_index']:04d}",
        },
    }
    selected_control = {
        key: baseline[key]
        for key in (
            "name",
            "kind",
            "checkpoint_path",
            "checkpoint_files",
            "predictions_path",
            "predictions_sha256",
            "validation_metrics",
            "per_crop_ms_p50",
            "inference_recipe",
        )
    }
    write_json(
        run / "validation-analysis.json",
        {"candidates": analyses, "test_evaluated": False, "model_execution": False},
    )
    evidence.extend(
        [
            state_path,
            run / "validation-analysis.json",
            run / "session.json",
            run / "execution-source.json",
        ]
    )
    return freeze(
        [selected_control, adapted],
        lineage=json.loads((run / "session.json").read_text()),
        evidence=evidence,
        output=run / "winner-frozen.json",
    )
