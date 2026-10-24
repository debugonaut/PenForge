"""The bounded R5 model experiment: warm start, pass training, validation selection.

Part A owns data preparation, the authoritative scorer and the final-test wrapper. This
module receives train/validation rows and a scoring callback, never final-test rows, and
never evaluates a final test. Importing it neither imports a model framework nor loads
weights; only :func:`run_bounded_training` does, and only when `owner_run=True`.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import json
import math
import random
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.english.r5_model import augment as augment_module
from research.english.r5_model import batching as batching_module
from research.english.r5_model import checkpoint as checkpoint_module
from research.english.r5_model import evaluate as evaluate_module
from research.english.r5_model import objective as objective_module
from research.english.r5_model import parameters as parameters_module
from research.english.r5_model import preprocess as preprocess_module
from research.english.r5_model import scheduler as scheduler_module
from research.english.r5_model.budgets import BudgetExceeded, BudgetPlan, RunBudget
from research.english.r5_model.recipe import R5ModelRecipe, load_recipe, recipe_hash
from research.english.r5_model.rows import CLASSES, StudyRows, load_study
from research.english.r5_model.selection import PassResult, rank_key, select_best
from research.pretrained.artifacts import (
    ROOT,
    canonical_hash,
    digest_file,
    verify_bundle,
)

REPORT_VERSION = "english-r5-model-report-v1"
INIT_FILES = frozenset({"config.json", "generation_config.json", "model.safetensors"})
REQUIRED_LIBRARIES = ("torch", "transformers", "safetensors", "tokenizers", "numpy", "pillow")
SOURCE_FILES = (
    "research/english/r5_model/__init__.py",
    "research/english/r5_model/augment.py",
    "research/english/r5_model/batching.py",
    "research/english/r5_model/budgets.py",
    "research/english/r5_model/checkpoint.py",
    "research/english/r5_model/evaluate.py",
    "research/english/r5_model/experiment.py",
    "research/english/r5_model/objective.py",
    "research/english/r5_model/parameters.py",
    "research/english/r5_model/preprocess.py",
    "research/english/r5_model/recipe.py",
    "research/english/r5_model/rows.py",
    "research/english/r5_model/runtime.py",
    "research/english/r5_model/scheduler.py",
    "research/english/r5_model/selection.py",
    "research/configs/english-r5-model.json",
    "research/pretrained/artifacts.py",
    "research/pretrained/runtime.py",
    "research/pretrained/study.py",
    "pyproject.toml",
    "uv.lock",
)


@dataclass(frozen=True)
class TrainingPlan:
    staging: Path
    mapping_name: str
    model_bundle: Path
    init_checkpoint: Path
    output: Path
    recipe: R5ModelRecipe
    recipe_path: Path


@dataclass(frozen=True)
class Preflight:
    plan: TrainingPlan
    study: StudyRows
    lineage: dict[str, Any]
    lineage_hash: str
    bundle_receipt: dict[str, Any]
    bundle_hash: str
    init_inventory: dict[str, str]
    init_inventory_hash: str
    source_inventory: dict[str, str]

    @property
    def recipe(self) -> R5ModelRecipe:
        return self.plan.recipe


def load_plan(
    *,
    staging: Path,
    mapping_name: str,
    model_bundle: Path,
    init_checkpoint: Path,
    output: Path,
    recipe_path: Path | None = None,
) -> TrainingPlan:
    config_path = (
        ROOT / "research/configs/english-r5-model.json" if recipe_path is None else recipe_path
    )
    recipe = load_recipe(config_path)
    if recipe.initialization != "verified_r4_selected_checkpoint":
        raise ValueError("r5_initialization_policy_not_supported")
    return TrainingPlan(
        staging=Path(staging),
        mapping_name=mapping_name,
        model_bundle=Path(model_bundle),
        init_checkpoint=Path(init_checkpoint),
        output=Path(output),
        recipe=recipe,
        recipe_path=Path(config_path),
    )


def _checkpoint_inventory(folder: Path) -> dict[str, str]:
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("r5_initialization_checkpoint_missing_or_unsafe")
    items = sorted(folder.iterdir())
    if {item.name for item in items} != set(INIT_FILES):
        raise ValueError("r5_initialization_checkpoint_files_changed")
    for item in items:
        if item.is_symlink() or not item.is_file():
            raise ValueError("r5_initialization_requires_regular_files")
    return {item.name: digest_file(item) for item in items}


TOKEN_KEYS = ("decoder_start_token_id", "eos_token_id", "pad_token_id")


def _token_map(config: dict[str, Any]) -> dict[str, Any]:
    """Read token ids from the checkpoint root or the nested decoder config.

    The pinned bundle stores them only under ``decoder``; a checkpoint saved by
    ``save_pretrained`` from a loaded model stores them at the root. Both describe the
    same tokenizer ids, so normalise before comparing.
    """
    decoder = config.get("decoder", {})
    return {key: config[key] if key in config else decoder.get(key) for key in TOKEN_KEYS}


def _check_configs(init_checkpoint: Path, model_bundle: Path) -> None:
    init_path = init_checkpoint / "config.json"
    bundle_path = model_bundle / "config.json"
    if not init_path.is_file() or not bundle_path.is_file():
        raise ValueError("r5_configuration_files_missing")
    init = json.loads(init_path.read_text(encoding="utf-8"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    for config, label in ((init, "initialization"), (bundle, "bundle")):
        if (
            config.get("model_type") != "vision-encoder-decoder"
            or config.get("encoder", {}).get("model_type") != "vit"
            or config.get("decoder", {}).get("model_type") != "trocr"
        ):
            raise ValueError(f"r5_{label}_architecture_changed")
    init_tokens, bundle_tokens = _token_map(init), _token_map(bundle)
    if any(value is None for value in init_tokens.values()):
        raise ValueError("r5_initialization_token_map_incomplete")
    if init_tokens != bundle_tokens:
        raise ValueError("r5_initialization_token_map_differs_from_pinned_bundle")
    weights = init_checkpoint / "model.safetensors"
    bundle_weights = model_bundle / "model.safetensors"
    if bundle_weights.is_file() and weights.stat().st_size != bundle_weights.stat().st_size:
        raise ValueError("r5_initialization_weight_count_differs_from_pinned_bundle")


def _source_inventory() -> dict[str, str]:
    inventory: dict[str, str] = {}
    for name in SOURCE_FILES:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"r5_source_file_missing:{name}")
        inventory[name] = digest_file(path)
    return inventory


def _library_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in REQUIRED_LIBRARIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise ValueError(f"r5_pinned_environment_missing:{name}") from None
    return versions


def _callback_lineage(scorer: Callable[..., Any]) -> dict[str, Any]:
    """Pin the authoritative metric implementation without importing it early."""
    target = scorer if inspect.isfunction(scorer) else getattr(scorer, "__call__", None)
    source = inspect.getsourcefile(target) if target is not None else None
    module = getattr(target, "__module__", None) or type(scorer).__module__
    qualname = getattr(target, "__qualname__", type(scorer).__qualname__)
    if source is None or not Path(source).is_file():
        return {
            "module": module,
            "qualname": qualname,
            "source_sha256": None,
            "limitation": "scorer_source_file_unavailable",
        }
    return {
        "module": module,
        "qualname": qualname,
        "source_sha256": digest_file(Path(source)),
    }


def _output_target(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("r5_output_must_not_be_a_symlink")
    resolved = path.resolve()
    if resolved == ROOT or (
        resolved.is_relative_to(ROOT) and not resolved.is_relative_to(ROOT / "research/runs")
    ):
        raise ValueError("r5_output_must_be_private_run_directory_or_external")
    return resolved


def _verify_coverage(study: StudyRows) -> dict[str, Any]:
    for split, rows in study.splits.items():
        labels = {row["label"] for row in rows}
        if labels != set(CLASSES):
            raise ValueError(f"r5_{split}_split_missing_case_sensitive_classes")
    return study.lineage()


def preflight(
    plan: TrainingPlan,
    *,
    bundle_verifier: Callable[[Path], dict[str, Any]] = verify_bundle,
    scorer: Callable[..., Any] | None = None,
) -> Preflight:
    """Metadata-only checks: rows, hashes, initialization lineage and environment."""
    study = load_study(plan.staging, plan.mapping_name, verify_bytes=True)
    receipt = bundle_verifier(plan.model_bundle)
    init_inventory = _checkpoint_inventory(plan.init_checkpoint)
    _check_configs(plan.init_checkpoint, plan.model_bundle)
    output = _output_target(plan.output)
    for protected in (plan.staging.resolve(), plan.model_bundle.resolve()):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("r5_output_must_be_separate_from_inputs")
    if plan.init_checkpoint.resolve().is_relative_to(output) or output.is_relative_to(
        plan.init_checkpoint.resolve()
    ):
        raise ValueError("r5_output_must_be_separate_from_initialization")
    rows_lineage = _verify_coverage(study)
    inventory_hash = canonical_hash(init_inventory)
    if plan.recipe_path.is_symlink() or not plan.recipe_path.is_file():
        raise ValueError("r5_recipe_configuration_missing_or_unsafe")
    source_inventory = _source_inventory()
    lineage: dict[str, Any] = {
        "recipe": plan.recipe.model_dump(),
        "recipe_hash": recipe_hash(plan.recipe),
        "recipe_config_sha256": digest_file(plan.recipe_path),
        "bundle": receipt,
        "bundle_hash": canonical_hash(receipt),
        "initialization": {
            "path": str(plan.init_checkpoint.resolve()),
            "files": init_inventory,
            "sha256": inventory_hash,
            "policy": plan.recipe.initialization,
        },
        "rows": rows_lineage,
        "source_files": source_inventory,
        "libraries": _library_versions(),
        "preprocessing": {
            "training": plan.recipe.training_preprocessing,
            "validation": plan.recipe.validation_preprocessing,
        },
        "test_access": "not_requested_by_part_b",
    }
    if scorer is not None:
        lineage["scoring_callback"] = _callback_lineage(scorer)
    return Preflight(
        plan=plan,
        study=study,
        lineage=lineage,
        lineage_hash=canonical_hash(lineage),
        bundle_receipt=receipt,
        bundle_hash=canonical_hash(receipt),
        init_inventory=init_inventory,
        init_inventory_hash=inventory_hash,
        source_inventory=source_inventory,
    )


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _write_predictions_atomic(path: Path, records: Sequence[Any]) -> str:
    temporary = path.with_suffix(path.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    sha = evaluate_module.write_predictions(temporary, records)
    temporary.replace(path)
    return sha


def _state_paths(output: Path) -> tuple[Path, Path]:
    return output / "latest", output / "best"


def _publish_latest(
    output: Path,
    *,
    named: Sequence[tuple[str, Any]],
    optimizer: Any,
    torch: Any,
    python_random: random.Random,
    lineage: checkpoint_module.StateLineage,
    pass_index: int,
    optimizer_step: int,
) -> checkpoint_module.TrainingState:
    staged = output / "latest.new"
    if staged.exists():
        shutil.rmtree(staged)
    state = checkpoint_module.save_state(
        staged,
        model_named=list(named),
        optimizer=optimizer,
        torch=torch,
        python_random=python_random,
        lineage=lineage,
        pass_index=pass_index,
        optimizer_step=optimizer_step,
    )
    latest, _ = _state_paths(output)
    if latest.exists() or latest.is_symlink():
        shutil.rmtree(latest)
    staged.rename(latest)
    return state


def _selected_candidate(output: Path) -> Path:
    return output / "selected-candidate"


def _write_selected_candidate(
    output: Path,
    *,
    named: Sequence[tuple[str, Any]],
    lineage: checkpoint_module.StateLineage,
    pass_index: int,
    source_state_sha256: str,
) -> checkpoint_module.SelectedState:
    """Write this pass's model-only snapshot before the ranking decision."""
    candidate = _selected_candidate(output)
    if candidate.exists() or candidate.is_symlink():
        if candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("r5_selected_candidate_not_a_directory")
        shutil.rmtree(candidate)
    return checkpoint_module.save_selected(
        candidate,
        model_named=list(named),
        lineage=lineage,
        pass_index=pass_index,
        source_state_sha256=source_state_sha256,
    )


def _promote_selected(output: Path) -> None:
    """Rename the candidate to ``best/`` after removing only the previous model-only snapshot."""
    candidate = _selected_candidate(output)
    _, best_dir = _state_paths(output)
    if (
        best_dir.resolve().parent != output.resolve()
        or candidate.resolve().parent != output.resolve()
    ):
        raise ValueError("r5_selected_checkpoint_outside_run_root")
    if best_dir.exists() or best_dir.is_symlink():
        if best_dir.is_symlink() or not best_dir.is_dir():
            raise ValueError("r5_selected_checkpoint_target_not_a_directory")
        shutil.rmtree(best_dir)
    candidate.rename(best_dir)


def _train_pass(
    torch: Any,
    model: Any,
    optimizer: Any,
    processor: Any,
    tokenizer: Any,
    rows: Sequence[Mapping[str, Any]],
    staging: Path,
    recipe: R5ModelRecipe,
    *,
    pass_index: int,
    base_step: int,
    total_steps: int,
    output: Path,
    budget: RunBudget,
) -> dict[str, Any]:
    parameters_module.set_eval_mode(model)
    parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    groups = batching_module.epoch_batches(rows, recipe.trocr_batch_size, recipe.seed, pass_index)
    steps_per_pass = math.ceil(len(groups) / recipe.trocr_accumulate_batches)
    seen: list[str] = []
    augmented = 0
    losses: list[float] = []
    optimizer_steps = 0
    started = time.monotonic()
    log_path = output / f"pass-{pass_index:04d}-loss-private.jsonl"
    with log_path.open("w", encoding="utf-8") as log:
        for start in range(0, len(groups), recipe.trocr_accumulate_batches):
            accumulation = groups[start : start + recipe.trocr_accumulate_batches]
            budget.checkpoint()
            optimizer.zero_grad(set_to_none=True)
            combined = 0.0
            for group in accumulation:
                labels = batching_module.padded_label_ids(
                    torch, tokenizer, [row["label"] for row in group], recipe.max_label_tokens
                )
                values = torch.cat(
                    [
                        preprocess_module.training_pixels(
                            processor, staging, row, recipe, epoch=pass_index
                        )
                        for row in group
                    ],
                    dim=0,
                )
                augmented += sum(
                    1
                    for row in group
                    if augment_module.parameters_for(recipe, sample_id=row["id"], epoch=pass_index)
                    is not None
                )
                loss = objective_module.teacher_forced_loss(torch, model, values, labels)
                value = float(loss.detach())
                losses.append(value)
                combined += value / len(accumulation)
                (loss / len(accumulation)).backward()
                seen.extend(row["id"] for row in group)
            torch.nn.utils.clip_grad_norm_(
                parameters, recipe.clip_grad_norm, error_if_nonfinite=True
            )
            optimizer_steps += 1
            step = base_step + optimizer_steps
            multiplier = scheduler_module.learning_rate_multiplier(
                step,
                total_steps=total_steps,
                warmup_steps=recipe.warmup_optimizer_steps,
                final_ratio=recipe.final_learning_rate_ratio,
            )
            scheduler_module.apply_multiplier(optimizer, recipe.trocr_learning_rate, multiplier)
            optimizer.step()
            log.write(
                json.dumps(
                    {
                        "step": step,
                        "pass": pass_index,
                        "loss": combined,
                        "learning_rate": recipe.trocr_learning_rate * multiplier,
                    }
                )
                + "\n"
            )
            print(
                json.dumps(
                    {
                        "stage": "r5-train",
                        "pass": pass_index,
                        "completed_steps": optimizer_steps,
                        "total_steps": steps_per_pass,
                    }
                ),
                flush=True,
            )
    batching_module.verify_coverage(rows, groups)
    if len(seen) != len(rows) or len(set(seen)) != len(rows):
        raise ValueError("r5_pass_did_not_use_every_training_row_exactly_once")
    return {
        "pass_index": pass_index,
        "status": "complete_one_pass",
        "samples": len(seen),
        "unique_samples": len(set(seen)),
        "batches": len(groups),
        "optimizer_steps": optimizer_steps,
        "accumulation_batches": recipe.trocr_accumulate_batches,
        "augmented_samples": augmented,
        "first_loss": losses[0] if losses else None,
        "last_loss": losses[-1] if losses else None,
        "mean_loss": sum(losses) / len(losses) if losses else None,
        "encoder_frozen": True,
        "trainable_scope": parameters_module.TRAINABLE_SCOPE,
        "wall_seconds": time.monotonic() - started,
    }


def run_bounded_training(
    plan: TrainingPlan,
    scorer: Callable[..., Mapping[str, Any]],
    *,
    owner_run: bool,
    runtime: Any | None = None,
    resume: bool = False,
    bundle_verifier: Callable[[Path], dict[str, Any]] = verify_bundle,
    enforce_cpu_limit: bool = True,
    budget_factory: Callable[[BudgetPlan, Path], Any] | None = None,
) -> dict[str, Any]:
    """Run at most ``recipe.pass_limit`` passes and select on validation only."""
    if not owner_run:
        raise ValueError("r5_model_execution_requires_owner_run_acknowledgment")
    evaluate_module.scorer_arity(scorer)
    prepared = preflight(plan, bundle_verifier=bundle_verifier, scorer=scorer)
    recipe = prepared.recipe
    output = _output_target(plan.output)
    if resume:
        if output.is_symlink() or not output.is_dir():
            raise ValueError("r5_resume_output_directory_missing")
        if not (output / "latest" / "state.json").is_file():
            raise ValueError("r5_resume_requires_a_pass_complete_state")
        report_path = output / "training-report.json"
        if report_path.is_file():
            existing = json.loads(report_path.read_text(encoding="utf-8"))
            if existing.get("status") == "training_complete_validation_selected":
                raise ValueError("r5_completed_run_must_not_be_resumed_in_place")
    else:
        if output.exists():
            raise ValueError("r5_output_directory_must_be_new")
        output.mkdir(parents=True, mode=0o700)

    if runtime is None:
        from research.english.r5_model.runtime import ResearchRuntime

        runtime = ResearchRuntime(plan.model_bundle)

    budget_plan = BudgetPlan.from_recipe(recipe)
    budget = (
        RunBudget(budget_plan, output)
        if budget_factory is None
        else budget_factory(budget_plan, output)
    )
    python_random = random.Random(recipe.seed)
    state_lineage = checkpoint_module.StateLineage(
        lineage_hash=prepared.lineage_hash,
        recipe_hash=recipe_hash(recipe),
        bundle_hash=prepared.bundle_hash,
        init_checkpoint_sha256=prepared.init_inventory_hash,
    )
    started_wall, started_cpu = time.monotonic(), time.process_time()
    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "status": "running",
        "stage": "r5_training",
        "task": "letters",
        "capability_status": "research_only",
        "qualified_for_product": False,
        "product_qualification": "none",
        "model_inference": True,
        "test_evaluated": False,
        "final_test": "not_run_by_part_b",
        "lineage_hash": prepared.lineage_hash,
        "lineage": prepared.lineage,
        "initialization": prepared.lineage["initialization"],
        "python": sys.version,
        "device": "cpu",
        "resume": {"requested": resume, "granularity": "pass_boundary_only"},
    }
    try:
        if enforce_cpu_limit:
            budget.install_cpu_limit()
        budget.start()
        with budget.phase("setup", recipe.max_pass_wall_seconds):
            torch = runtime.setup(recipe)
            tokenizer = runtime.tokenizer()
            processor = runtime.processor()
            model = runtime.load_initial_model(plan.init_checkpoint, tokenizer)
            parameters = parameters_module.select_trainable(model)
            named = parameters_module.named_trainable(model)
            trainable_inventory = parameters_module.inventory(named)
            optimizer = torch.optim.AdamW(
                parameters,
                lr=recipe.trocr_learning_rate,
                weight_decay=recipe.weight_decay,
            )
            parameters_module.set_eval_mode(model)

        stale_candidate = _selected_candidate(output)
        if stale_candidate.exists() or stale_candidate.is_symlink():
            if stale_candidate.is_symlink() or not stale_candidate.is_dir():
                raise ValueError("r5_stale_selected_candidate_not_a_directory")
            shutil.rmtree(stale_candidate)
        results: list[PassResult] = []
        state: checkpoint_module.TrainingState | None = None
        if resume:
            tensors, state = checkpoint_module.load_state(output / "latest")
            checkpoint_module.verify_resume(
                state,
                lineage=state_lineage,
                trainable_inventory=trainable_inventory,
                pass_limit=recipe.pass_limit,
            )
            checkpoint_module.restore_state(model, optimizer, tensors, state)
            checkpoint_module.restore_rng(torch, python_random, state)
            results = _load_results(output, expected=state.pass_index)
            report["resume"].update(resumed_from_pass=state.pass_index)

        groups_per_pass = math.ceil(len(prepared.study.train) / recipe.trocr_batch_size)
        steps_per_pass = math.ceil(groups_per_pass / recipe.trocr_accumulate_batches)
        planned_total_steps = steps_per_pass * recipe.pass_limit
        first_pass = (state.pass_index + 1) if state is not None else 1
        best: PassResult | None = None
        if results:
            best = min(results, key=rank_key)

        for pass_index in range(first_pass, recipe.pass_limit + 1):
            with budget.phase(f"train-{pass_index}", recipe.max_pass_wall_seconds):
                training = _train_pass(
                    torch,
                    model,
                    optimizer,
                    processor,
                    tokenizer,
                    prepared.study.train,
                    prepared.study.staging,
                    recipe,
                    pass_index=pass_index,
                    base_step=(pass_index - 1) * steps_per_pass,
                    total_steps=planned_total_steps,
                    output=output,
                    budget=budget,
                )
            validation_started = time.monotonic()
            with budget.phase(f"validation-{pass_index}", recipe.max_validation_wall_seconds):
                records, observed = evaluate_module.generate_predictions(
                    torch,
                    model,
                    tokenizer,
                    processor,
                    prepared.study.validation,
                    prepared.study.staging,
                    recipe,
                    stage=f"pass-{pass_index:04d}",
                )
                evaluate_module.verify_records(records, prepared.study.validation)
                predictions_path = (
                    output / f"pass-{pass_index:04d}-validation-predictions-private.jsonl"
                )
                predictions_sha = _write_predictions_atomic(predictions_path, records)
                score = evaluate_module.validate_score(
                    evaluate_module.call_scorer(scorer, records, prepared.study.validation),
                    samples=len(records),
                )
                _write_json(
                    output / f"pass-{pass_index:04d}-score.json",
                    {"score": score, "observed": observed},
                )
            validation_seconds = time.monotonic() - validation_started

            optimizer_step = (state.optimizer_step if state is not None else 0) + training[
                "optimizer_steps"
            ]
            state = _publish_latest(
                output,
                named=named,
                optimizer=optimizer,
                torch=torch,
                python_random=python_random,
                lineage=state_lineage,
                pass_index=pass_index,
                optimizer_step=optimizer_step,
            )
            selected_state = _write_selected_candidate(
                output,
                named=named,
                lineage=state_lineage,
                pass_index=pass_index,
                source_state_sha256=state.tensors_sha256,
            )
            latency = (
                score["per_crop_ms_p50"]
                if score["per_crop_ms_p50"] is not None
                else observed["per_crop_ms_p50"]
            )
            result = PassResult(
                pass_index=pass_index,
                accuracy=score["accuracy"],
                correct=score["correct"],
                n=score["n"],
                scalar_cer=score["scalar_cer"],
                per_crop_ms_p50=latency,
                exact_case_sensitive=True,
                checkpoint=f"pass-{pass_index:04d}",
                checkpoint_sha256=selected_state.tensors_sha256,
                predictions_sha256=predictions_sha,
            )
            results.append(result)
            _write_results(output, results)
            best = min(results, key=rank_key)
            if best.pass_index == pass_index:
                _promote_selected(output)
            else:
                shutil.rmtree(_selected_candidate(output))
            report.setdefault("passes", []).append(
                {
                    **training,
                    "validation_seconds": validation_seconds,
                    "validation": result.model_dump(),
                    "observed": observed,
                }
            )
            print(
                json.dumps(
                    {
                        "stage": "r5-validation",
                        "pass": pass_index,
                        "exact_accuracy": result.accuracy,
                        "validation_samples": result.n,
                    }
                ),
                flush=True,
            )
            if recipe.early_stopping_patience and _passes_since_best(results) >= (
                recipe.early_stopping_patience
            ):
                report["early_stopped_after_pass"] = pass_index
                break

        if best is None or not results:
            raise ValueError("r5_completed_run_has_no_validation_result")
        selection = select_best(results)
        _, best_dir = _state_paths(output)
        best_tensors, best_state = checkpoint_module.load_selected(best_dir)
        if (
            best_state.tensors_sha256 != best.checkpoint_sha256
            or best_state.pass_index != best.pass_index
        ):
            raise ValueError("r5_best_checkpoint_differs_from_validation_winner")
        del best_tensors
        _write_json(output / "selection.json", selection)
        _verify_unchanged(prepared, bundle_verifier)
        report.update(
            status="training_complete_validation_selected",
            selection=selection,
            selected_checkpoint={
                "path": str(best_dir),
                "pass": best.pass_index,
                "weights_sha256": best_state.tensors_sha256,
                "source_state_sha256": best_state.source_state_sha256,
            },
            pass_results=[result.model_dump() for result in results],
            training={
                "passes_completed": len(results),
                "optimizer_steps": state.optimizer_step if state else 0,
                "samples_per_pass": len(prepared.study.train),
                "validation_samples": len(prepared.study.validation),
                "scheduler": recipe.scheduler,
                "augmentation": recipe.augmentation,
                "resume_granularity": "pass_boundary_only",
                "selection_rule": selection["rule"],
            },
            budgets=budget.report(),
            wall_seconds=time.monotonic() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
        )
    except BudgetExceeded as error:
        report.update(
            status="incomplete_budget_exceeded",
            reason=str(error),
            selection=None,
            budgets=budget.report(),
            wall_seconds=time.monotonic() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
        )
        _write_json(output / "training-report.json", report)
        return report
    except BaseException as error:
        _write_json(
            output / "failure.json",
            {
                "status": "failed_or_incomplete",
                "exception_type": type(error).__name__,
                "error_detail_private": str(error)[:1000],
                "qualified_for_product": False,
            },
        )
        raise
    finally:
        budget.close()
    _write_json(output / "training-report.json", report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "passes": len(results),
                "selected_pass": best.pass_index,
                "test_evaluated": False,
            }
        ),
        flush=True,
    )
    return report


def _passes_since_best(results: Sequence[PassResult]) -> int:
    ranked = min(range(len(results)), key=lambda index: rank_key(results[index]))
    return len(results) - 1 - ranked


def _write_results(output: Path, results: Sequence[PassResult]) -> None:
    _write_json(
        output / "pass-results.json",
        {"version": "english-r5-pass-results-v1", "results": [r.model_dump() for r in results]},
    )


def _load_results(output: Path, *, expected: int) -> list[PassResult]:
    path = output / "pass-results.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("r5_resume_requires_pass_results")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != "english-r5-pass-results-v1":
        raise ValueError("r5_pass_results_version_changed")
    results = [PassResult.model_validate(item) for item in payload.get("results", [])]
    if len(results) != expected or [item.pass_index for item in results] != list(
        range(1, expected + 1)
    ):
        raise ValueError("r5_pass_results_do_not_match_resumed_state")
    return results


def _verify_unchanged(
    prepared: Preflight, bundle_verifier: Callable[[Path], dict[str, Any]]
) -> None:
    plan = prepared.plan
    if _checkpoint_inventory(plan.init_checkpoint) != prepared.init_inventory:
        raise ValueError("r5_initialization_checkpoint_changed_during_run")
    if canonical_hash(bundle_verifier(plan.model_bundle)) != prepared.bundle_hash:
        raise ValueError("r5_model_bundle_changed_during_run")
    if _source_inventory() != prepared.source_inventory:
        raise ValueError("r5_source_code_changed_during_run")
