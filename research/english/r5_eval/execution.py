"""Part A integration of the completed Part B package. No execution on import."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path

from research.english.r5_data.check import check, training_inputs
from research.english.r5_data.common import (
    canonical_hash,
    checked,
    digest_file,
    read_jsonl,
    write_json,
)
from research.english.r5_eval.lifecycle import (
    claim_final_test,
    file_inventory,
    freeze,
    verify_frozen,
)
from research.english.r5_eval.predict import predict_rows
from research.english.r5_eval.scoring import score_predictions
from research.pretrained.artifacts import ROOT

# The completed Part B repair provides sufficient finite limits and a weights-only
# winner. Copy its recipe exactly, without modifying its configuration or ceilings.
RESOURCE_RECIPE: dict = {}
EFFECTIVE_OUTPUT_BYTES = 3_000_000_000
SESSION_VERSION = "english-r5-integrated-session-v1"


def source_inventory() -> dict:
    paths = [
        ROOT / "research/english/r5_contract.py",
        ROOT / "research/english/r5_run.py",
        ROOT / "scripts/train_english_r5_research.sh",
        ROOT / "research/configs/english-r5-data.json",
        ROOT / "research/evaluation/harness.py",
    ]
    for folder in ("r5_data", "r5_eval"):
        paths.extend(sorted((ROOT / "research/english" / folder).glob("*.py")))
    return {str(path.relative_to(ROOT)): digest_file(path) for path in paths}


def private_run(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("r5_run_root_must_not_be_symlink")
    path = path.resolve()
    if path == ROOT or (
        path.is_relative_to(ROOT) and not path.is_relative_to(ROOT / "research/runs")
    ):
        raise ValueError("r5_run_outputs_must_be_external_or_ignored_research_runs")
    return path


def bounded_budget(plan, output):
    """Public Part B injection seam, preserving the declared finite disk ceiling."""
    from research.english.r5_model.budgets import RunBudget

    return RunBudget(replace(plan, output_bytes=EFFECTIVE_OUTPUT_BYTES), output)


def prepare_initialization(source: Path, bundle: Path, run: Path) -> tuple[Path, dict]:
    """Canonicalize composite-vs-decoder token metadata without changing weight bytes.

    Transformers save_pretrained hoists these three IDs into the composite config.
    Part B compares raw composite keys. Preserve the original, and give that strict
    comparison a derived serialization whose explicit decoder IDs retain the same
    effective runtime values used by the shared strict loader.
    """
    old = json.loads(checked(source, "config.json").read_text())
    base = json.loads(checked(bundle, "config.json").read_text())
    derived = json.loads(json.dumps(old))
    effective = {}
    for key in ("decoder_start_token_id", "eos_token_id", "pad_token_id"):
        expected = base["decoder"].get(key)
        observed = old.get(key) if old.get(key) is not None else old["decoder"].get(key)
        if type(expected) is not int or observed != expected:
            raise ValueError("r5_token_metadata_adapter_requires_matching_observed_effective_ids")
        effective[key] = expected
        derived.pop(key, None)
        derived["decoder"][key] = expected
    target = run / "initialization-canonical"
    original_inventory = file_inventory(source)
    if target.exists():
        if target.is_symlink() or json.loads(checked(target, "config.json").read_text()) != derived:
            raise ValueError("r5_derived_initialization_metadata_changed")
    else:
        target.mkdir(mode=0o700)
        write_json(target / "config.json", derived)
        # Immutable read-only source references. Never chmod or write linked files.
        os.link(checked(source, "model.safetensors"), target / "model.safetensors")
        os.link(checked(source, "generation_config.json"), target / "generation_config.json")
    actual = file_inventory(target)
    if set(actual) != set(original_inventory) or any(
        actual[name] != original_inventory[name]
        for name in ("model.safetensors", "generation_config.json")
    ):
        raise ValueError("r5_derived_initialization_weight_or_generation_bytes_changed")
    return target, {
        "policy": "explicit_equivalent_decoder_token_ids_v1",
        "effective_token_ids": effective,
        "original_path": str(source.resolve()),
        "original_files": original_inventory,
        "derived_path": str(target.resolve()),
        "derived_files": actual,
        "weights_changed": False,
        "original_files_changed": False,
    }


def context(
    staging: Path,
    model: Path,
    initialization: Path,
    r4_winner: Path,
    run: Path,
    *,
    include_test=False,
):
    from research.english.r5_model import load_plan, load_recipe, preflight

    study = check(staging, include_test=include_test)
    inputs = training_inputs(study)
    run = private_run(run)
    for protected in (staging.resolve(), model.resolve(), initialization.resolve()):
        if run.is_relative_to(protected) or protected.is_relative_to(run):
            raise ValueError("r5_run_must_be_separate_from_immutable_inputs")
    if not (ROOT / "research/ENGLISH_R5_MODEL.md").is_file():
        raise ValueError("r5_requires_part_b_documented_completion")
    if r4_winner.is_symlink() or not r4_winner.is_file():
        raise ValueError("r5_requires_actual_r4_selected_checkpoint_evidence")
    old = json.loads(r4_winner.read_text())
    candidate = old.get("winner", {})
    if (
        old.get("status") != "frozen_before_test"
        or candidate.get("kind") != "trocr_full_pass"
        or Path(candidate.get("checkpoint_path", "")).resolve() != initialization.resolve()
        or file_inventory(initialization) != candidate.get("checkpoint_files")
    ):
        raise ValueError("r5_initialization_must_match_frozen_r4_winner")
    base_recipe = load_recipe().model_dump()
    effective_recipe = {**base_recipe, **RESOURCE_RECIPE}
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    recipe_path = run / "execution-recipe.json"
    if recipe_path.exists():
        if recipe_path.is_symlink() or json.loads(recipe_path.read_text()) != effective_recipe:
            raise ValueError("r5_frozen_execution_recipe_changed")
    else:
        write_json(recipe_path, effective_recipe)
    derived_initialization, initialization_adapter = prepare_initialization(
        initialization, model, run
    )
    plan = load_plan(
        staging=staging,
        mapping_name="train-validation.jsonl",
        model_bundle=model,
        init_checkpoint=derived_initialization,
        output=run / "training",
        recipe_path=recipe_path,
    )
    prepared = preflight(plan, scorer=inputs.score_validation)
    lineage = {
        "version": SESSION_VERSION,
        "data_preparation_sha256": study["preparation_sha256"],
        "model_lineage_hash": prepared.lineage_hash,
        "model_lineage": prepared.lineage,
        "part_a_sources": source_inventory(),
        "r4_winner_sha256": digest_file(r4_winner),
        "execution_source_manifest_sha256": digest_file(
            Path(os.environ["PENFORGE_R5_SOURCE_MANIFEST"])
        )
        if "PENFORGE_R5_SOURCE_MANIFEST" in os.environ
        else None,
        "final_claim_ledger": str(
            Path(
                os.environ.get(
                    "PENFORGE_R5_LEDGER", str(ROOT / "research/runs/r5-final-test-claims")
                )
            ).resolve()
        ),
        "initialization_adapter": initialization_adapter,
        "resource_integration": {
            "private_recipe_overrides": RESOURCE_RECIPE,
            "effective_output_bytes": EFFECTIVE_OUTPUT_BYTES,
            "reason": "Completed Part B limits and weights-only winner; no recipe override or sample/pass truncation.",
            "unchanged_model_recipe_sha256": digest_file(
                ROOT / "research/configs/english-r5-model.json"
            ),
        },
        "test_policy": "separate_invocation_after_frozen_validation_winner_no_automatic_retry",
    }
    session_path = run / "session.json"
    if session_path.exists():
        if session_path.is_symlink() or json.loads(session_path.read_text()) != lineage:
            raise ValueError("r5_session_inputs_code_or_environment_changed")
    else:
        write_json(session_path, lineage)
    return {
        "study": study,
        "inputs": inputs,
        "plan": plan,
        "prepared": prepared,
        "lineage": lineage,
        "run": run,
        "staging": staging,
        "model": model,
        "initialization": derived_initialization,
    }


def require_execution(owner_run: bool):
    if owner_run is not True:
        raise ValueError("r5_model_execution_requires_explicit_owner_run_acknowledgment")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false")


def _runtime(ctx):
    from research.english.r5_model.runtime import ResearchRuntime

    runtime = ResearchRuntime(ctx["model"])
    torch = runtime.setup(ctx["plan"].recipe)
    tokenizer = runtime.tokenizer()
    processor = runtime.processor()
    model = runtime.load_initial_model(ctx["initialization"], tokenizer)
    return torch, model, processor, tokenizer


def _evaluation_budget(ctx, output):
    from research.english.r5_model.budgets import BudgetPlan, RunBudget

    recipe = ctx["plan"].recipe
    plan = BudgetPlan(
        recipe.max_validation_wall_seconds,
        recipe.max_validation_wall_seconds,
        recipe.max_validation_wall_seconds,
        3600,
        recipe.max_rss_mb,
        100_000_000,
    )
    budget = RunBudget(plan, output)
    budget.install_cpu_limit()
    budget.start()
    return budget


def baseline(ctx, *, owner_run: bool):
    require_execution(owner_run)
    output = ctx["run"] / "baseline"
    output.mkdir(mode=0o700)
    budget = _evaluation_budget(ctx, output)
    try:
        with budget.phase("r4_control_validation", ctx["plan"].recipe.max_validation_wall_seconds):
            torch, model, processor, tokenizer = _runtime(ctx)
            rows = ctx["study"]["selected"]["validation"]
            records, observed = predict_rows(
                torch, model, processor, tokenizer, rows, ctx["staging"], split="validation"
            )
            metrics = score_predictions(rows, records)
            prediction_path = output / "validation-predictions-private.jsonl"
            _write_records(prediction_path, records)
            value = {
                "version": "r5-control-report-v1",
                "status": "measured_validation_only",
                "lineage_hash": canonical_hash(ctx["lineage"]),
                "validation_metrics": metrics,
                "observed": observed,
                "predictions_sha256": digest_file(prediction_path),
                "model_inference": True,
                "training": "not_run",
                "test_evaluated": False,
                "resources": budget.report(),
            }
            write_json(output / "report.json", value)
            return value
    finally:
        budget.close()


def train(ctx, *, owner_run: bool):
    require_execution(owner_run)
    if not (ctx["run"] / "baseline/report.json").is_file():
        raise ValueError("r5_training_requires_separate_initial_control_validation")
    if (ctx["run"] / "winner-frozen.json").exists():
        raise ValueError("r5_training_forbidden_after_winner_freeze")
    from research.english.r5_model import run_bounded_training

    value = run_bounded_training(
        ctx["plan"], ctx["inputs"].score_validation, owner_run=True, budget_factory=bounded_budget
    )
    if value["status"] != "training_complete_validation_selected":
        raise ValueError("r5_training_incomplete_no_winner_or_final_test_permitted")
    return value


def _write_records(path, records):
    with path.open("x", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    path.chmod(0o600)


def freeze_session(ctx):
    run = ctx["run"]
    baseline_path, training_path = (
        run / "baseline/report.json",
        run / "training/training-report.json",
    )
    control = json.loads(checked(run, "baseline/report.json").read_text())
    report = json.loads(checked(run, "training/training-report.json").read_text())
    if (
        control.get("status") != "measured_validation_only"
        or control.get("test_evaluated") is not False
        or control.get("lineage_hash") != canonical_hash(ctx["lineage"])
    ):
        raise ValueError("r5_initial_control_incomplete_or_wrong_lineage")
    if (
        report.get("status") != "training_complete_validation_selected"
        or report.get("lineage_hash") != ctx["prepared"].lineage_hash
        or report.get("test_evaluated") is not False
    ):
        raise ValueError("r5_training_report_incomplete_or_wrong_lineage")
    rows = ctx["study"]["selected"]["validation"]
    control_predictions = checked(run, "baseline/validation-predictions-private.jsonl")
    control_metrics = score_predictions(rows, read_jsonl(control_predictions))
    if (
        digest_file(control_predictions) != control["predictions_sha256"]
        or control_metrics != control["validation_metrics"]
    ):
        raise ValueError("r5_control_predictions_or_scores_changed")
    evidence = [baseline_path, training_path, control_predictions]
    candidates = [
        {
            "name": "r4_initial_control",
            "kind": "r4_full_checkpoint",
            "checkpoint_path": str(ctx["initialization"].resolve()),
            "checkpoint_files": file_inventory(ctx["initialization"]),
            "validation_metrics": control_metrics,
            "per_crop_ms_p50": control["observed"]["per_crop_ms_p50"],
        }
    ]
    results, full_metrics = [], {}
    from research.english.r5_model.selection import PassResult, select_best

    train_n = len(ctx["study"]["selected"]["train"])
    passes = report.get("passes", [])
    if not passes or len(passes) != report["training"]["passes_completed"]:
        raise ValueError("r5_complete_pass_evidence_missing")
    for item in passes:
        result = PassResult.model_validate(item["validation"])
        if (
            item.get("status") != "complete_one_pass"
            or item.get("samples") != train_n
            or item.get("unique_samples") != train_n
        ):
            raise ValueError("r5_cannot_select_truncated_training_pass")
        prediction_path = checked(
            run, f"training/pass-{result.pass_index:04d}-validation-predictions-private.jsonl"
        )
        if digest_file(prediction_path) != result.predictions_sha256:
            raise ValueError("r5_pass_prediction_hash_changed")
        metrics = score_predictions(rows, read_jsonl(prediction_path))
        if (metrics["correct"], metrics["n"], metrics["accuracy"], metrics["scalar_cer"]) != (
            result.correct,
            result.n,
            result.accuracy,
            result.scalar_cer,
        ):
            raise ValueError("r5_authoritative_id_join_disagrees_with_model_selection")
        results.append(result)
        full_metrics[result.pass_index] = metrics
        evidence.append(prediction_path)
    if select_best(results) != report.get("selection"):
        raise ValueError("r5_validation_ranking_changed")
    selected = report["selection"]["winner"]
    best_path = run / "training/best"
    state_path = checked(best_path, "state.json")
    state = json.loads(state_path.read_text())
    if (
        state.get("status") != "pass_complete"
        or state.get("lineage_hash") != ctx["prepared"].lineage_hash
        or state.get("pass_index") != selected["pass_index"]
        or state.get("tensors_sha256") != selected["checkpoint_sha256"]
        or digest_file(checked(best_path, "state.safetensors")) != selected["checkpoint_sha256"]
    ):
        raise ValueError("r5_selected_checkpoint_does_not_match_validation_evidence")
    candidates.append(
        {
            "name": f"r5_pass_{selected['pass_index']:04d}",
            "kind": "r5_trainable_delta",
            "checkpoint_path": str(best_path.resolve()),
            "checkpoint_files": file_inventory(best_path),
            "validation_metrics": full_metrics[selected["pass_index"]],
            "per_crop_ms_p50": selected["per_crop_ms_p50"],
        }
    )
    return freeze(
        candidates, lineage=ctx["lineage"], evidence=evidence, output=run / "winner-frozen.json"
    )


def _apply_delta(torch, model, winner):
    from safetensors import safe_open

    from research.english.r5_model.parameters import inventory, named_trainable, select_trainable

    folder = Path(winner["checkpoint_path"])
    state = json.loads(checked(folder, "state.json").read_text())
    select_trainable(model)
    named = named_trainable(model)
    if inventory(named) != state["trainable_inventory"]:
        raise ValueError("r5_final_model_trainable_inventory_changed")
    with safe_open(
        str(checked(folder, "state.safetensors")), framework="pt", device="cpu"
    ) as weights:
        with torch.no_grad():
            for name, parameter in named:
                value = weights.get_tensor("model." + name)
                if list(value.shape) != list(parameter.shape) or value.dtype != parameter.dtype:
                    raise ValueError("r5_final_delta_shape_or_dtype_changed")
                parameter.copy_(value)


def final_test(ctx, *, owner_run: bool):
    require_execution(owner_run)
    run, study = ctx["run"], ctx["study"]
    frozen_path = run / "winner-frozen.json"
    frozen = verify_frozen(frozen_path, ctx["lineage"])
    groups = {
        name: study["selected"][key]
        for name, key in (("primary", "test"), ("school", "school_test"))
        if study["selected"].get(key)
    }
    if "primary" not in groups:
        raise ValueError("r5_fresh_primary_final_test_unavailable")
    rows = [row for items in groups.values() for row in items]
    output = run / "final-test"
    output.mkdir(mode=0o700)
    claim = claim_final_test(rows, frozen_path, Path(ctx["lineage"]["final_claim_ledger"]))
    write_json(output / "started.json", claim)
    budget = _evaluation_budget(ctx, output)
    started_wall, started_cpu = time.monotonic(), time.process_time()
    try:
        with budget.phase("one_final_session", ctx["plan"].recipe.max_validation_wall_seconds):
            torch, model, processor, tokenizer = _runtime(ctx)
            winner = frozen["winner"]
            if winner["kind"] == "r5_trainable_delta":
                _apply_delta(torch, model, winner)
            elif winner["kind"] != "r4_full_checkpoint":
                raise ValueError("r5_final_winner_kind_unsupported")
            results = {}
            for name, items in groups.items():
                records, observed = predict_rows(
                    torch, model, processor, tokenizer, items, ctx["staging"], split="test"
                )
                path = output / f"{name}-predictions-private.jsonl"
                _write_records(path, records)
                results[name] = {
                    "metrics": score_predictions(items, records),
                    "observed": observed,
                    "predictions_sha256": digest_file(path),
                }
            verify_frozen(frozen_path, ctx["lineage"])
            value = {
                "version": "english-r5-final-report-v1",
                "status": "measured_research_only",
                "frozen_winner_sha256": digest_file(frozen_path),
                "lineage_hash": canonical_hash(ctx["lineage"]),
                "winner": winner["name"],
                "groups": results,
                "claim": claim,
                "test_evaluated": True,
                "school_test": "measured_separately"
                if "school" in groups
                else "unavailable_insufficient_untouched_writers",
                "model_inference": True,
                "training": "not_run",
                "product_qualification": "none",
                "wall_seconds": time.monotonic() - started_wall,
                "cpu_seconds": time.process_time() - started_cpu,
                "resources": budget.report(),
            }
            write_json(output / "report.json", value)
            return value
    except BaseException as error:
        write_json(
            output / "failure.json",
            {
                "status": "incomplete_claim_retained",
                "exception": type(error).__name__,
                "automatic_retry": False,
            },
        )
        raise
    finally:
        budget.close()
