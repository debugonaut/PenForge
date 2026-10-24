"""The bounded R6 continuation experiment.

Part A owns data preparation, the authoritative scorer, the source freeze and the final
test. This module receives verified train/validation rows plus a scoring callback, starts
from the frozen R5 composition (R4 base + selected trainable delta), trains at most
``recipe.pass_limit`` complete passes, validates after each pass and returns the validation
evidence. It never reads a test row and never evaluates a test split.

Importing this module neither imports a model framework nor loads weights; only the
owner-run functions do, and only with ``owner_run=True``.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import json
import math
import random
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.english.r5_eval.lifecycle import file_inventory
from research.english.r5_model import batching as batching_module
from research.english.r5_model import objective as objective_module
from research.english.r5_model import parameters as parameters_module
from research.english.r5_model import scheduler as scheduler_module
from research.english.r6_contract import AggregateLimits, rank_key
from research.english.r6_model import checkpoint as checkpoint_module
from research.english.r6_model import evaluate as evaluate_module
from research.english.r6_model import initialization as initialization_module
from research.english.r6_model import preprocess as preprocess_module
from research.english.r6_model import rows as rows_module
from research.english.r6_model.budgets import (
    BudgetExceeded,
    BudgetPlan,
    Consumption,
    RunBudget,
    assert_publication_fits,
)
from research.english.r6_model.progress import Progress
from research.english.r6_model.recipe import (
    R6ModelRecipe,
    data_epoch,
    load_recipe,
    recipe_hash,
)
from research.pretrained.artifacts import ROOT, canonical_hash, digest_file, verify_bundle

REPORT_VERSION = "english-r6-model-report-v1"
RESULTS_VERSION = "english-r6-pass-results-v1"
SELECTION_VERSION = "english-r6-selection-v1"
CONTROL_VERSION = "english-r6-control-report-v1"
CONTROL_CANDIDATE = "r5_unchanged_control"
IN_PROGRESS_MARKER = "pass-in-progress.json"
# R5 measured 1.32 CPU seconds per wall second (14,588 / 11,034). A discarded attempt is
# charged 1.5x its wall ceiling to keep the aggregate CPU accounting conservative.
CPU_PER_WALL_CONSERVATIVE = 1.5
REQUIRED_LIBRARIES = ("torch", "transformers", "safetensors", "tokenizers", "numpy", "pillow")

R6_MODULE_FILES = (
    "__init__.py",
    "budgets.py",
    "checkpoint.py",
    "evaluate.py",
    "experiment.py",
    "initialization.py",
    "preprocess.py",
    "progress.py",
    "recipe.py",
    "rows.py",
    "runtime.py",
)
REUSED_R5_FILES = (
    "research/english/r5_model/augment.py",
    "research/english/r5_model/batching.py",
    "research/english/r5_model/evaluate.py",
    "research/english/r5_model/objective.py",
    "research/english/r5_model/parameters.py",
    "research/english/r5_model/preprocess.py",
    "research/english/r5_model/scheduler.py",
)
SHARED_FILES = (
    "research/english/r6_contract.py",
    "research/english/r5_contract.py",
    "research/english/r5_data/common.py",
    "research/english/r5_eval/lifecycle.py",
    "research/pretrained/artifacts.py",
    "research/pretrained/runtime.py",
    "research/pretrained/study.py",
    "research/configs/english-r6-model-continuation.json",
    "pyproject.toml",
    "uv.lock",
)
SOURCE_FILES = tuple(f"research/english/r6_model/{name}" for name in R6_MODULE_FILES) + (
    *REUSED_R5_FILES,
    *SHARED_FILES,
)


@dataclass(frozen=True)
class TrainingPlan:
    staging: Path
    mapping_name: str
    model_bundle: Path
    init_checkpoint: Path
    init_delta: Path
    r5_winner: Path
    output: Path
    recipe: R6ModelRecipe
    recipe_path: Path


@dataclass(frozen=True)
class Preflight:
    plan: TrainingPlan
    study: rows_module.StudyRows
    initialization: dict[str, Any]
    lineage: dict[str, Any]
    lineage_hash: str
    bundle_receipt: dict[str, Any]
    bundle_hash: str
    source_inventory: dict[str, str]
    publication_peak_bytes: int

    @property
    def recipe(self) -> R6ModelRecipe:
        return self.plan.recipe


def load_plan(
    *,
    staging: Path,
    mapping_name: str,
    model_bundle: Path,
    init_checkpoint: Path,
    init_delta: Path,
    r5_winner: Path,
    output: Path,
    recipe_path: Path | None = None,
) -> TrainingPlan:
    """Bind the verified inputs and the pinned recipe; nothing is read from the model."""
    config_path = (
        ROOT / "research/configs/english-r6-model-continuation.json"
        if recipe_path is None
        else Path(recipe_path)
    )
    recipe = load_recipe(config_path)
    if recipe.initialization != "verified_r4_base_plus_r5_selected_delta":
        raise ValueError("r6_initialization_policy_not_supported")
    if recipe.optimizer_initialization != "fresh_adamw_no_r5_optimizer_state":
        raise ValueError("r6_optimizer_initialization_policy_not_supported")
    return TrainingPlan(
        staging=Path(staging).resolve(),
        mapping_name=mapping_name,
        model_bundle=Path(model_bundle).resolve(),
        init_checkpoint=Path(init_checkpoint).resolve(),
        init_delta=Path(init_delta).resolve(),
        r5_winner=Path(r5_winner).resolve(),
        output=Path(output).resolve(),
        recipe=recipe,
        recipe_path=Path(config_path).resolve(),
    )


def _source_inventory() -> dict[str, str]:
    inventory: dict[str, str] = {}
    for name in SOURCE_FILES:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"r6_source_file_missing:{name}")
        inventory[name] = digest_file(path)
    return inventory


def _library_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in REQUIRED_LIBRARIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise ValueError(f"r6_pinned_environment_missing:{name}") from None
    return versions


def _callback_lineage(scorer: Callable[..., Any]) -> dict[str, Any]:
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
        raise ValueError("r6_output_must_not_be_a_symlink")
    resolved = path.resolve()
    if resolved == ROOT or (
        resolved.is_relative_to(ROOT) and not resolved.is_relative_to(ROOT / "research/runs")
    ):
        raise ValueError("r6_output_must_be_private_run_directory_or_external")
    return resolved


def _envelope(recipe: R6ModelRecipe) -> dict[str, int]:
    limits = AggregateLimits()
    if recipe.pass_limit > limits.total_passes:
        raise ValueError("r6_recipe_passes_exceed_aggregate_envelope")
    if recipe.max_run_wall_seconds > limits.wall_seconds:
        raise ValueError("r6_recipe_wall_exceeds_aggregate_envelope")
    if recipe.max_cpu_seconds > limits.cpu_seconds:
        raise ValueError("r6_recipe_cpu_exceeds_aggregate_envelope")
    if recipe.max_rss_mb > limits.rss_mb:
        raise ValueError("r6_recipe_rss_exceeds_aggregate_envelope")
    if recipe.max_output_bytes > limits.output_bytes:
        raise ValueError("r6_recipe_output_exceeds_aggregate_envelope")
    return {
        "pass_limit": recipe.pass_limit,
        "wall_seconds": recipe.max_run_wall_seconds,
        "cpu_seconds": recipe.max_cpu_seconds,
        "rss_mb": recipe.max_rss_mb,
        "output_bytes": recipe.max_output_bytes,
    }


def _check_bundle_agreement(plan: TrainingPlan) -> None:
    bundle_config = initialization_module.read_config(plan.model_bundle / "config.json")
    initialization_module.check_architecture(bundle_config, label="bundle")


def _initialization(
    plan: TrainingPlan, expected: initialization_module.ExpectedHashes | None
) -> dict[str, Any]:
    return initialization_module.verify_initialization(
        initialization_module.Initialization(
            base_checkpoint=plan.init_checkpoint,
            delta_checkpoint=plan.init_delta,
            winner_file=plan.r5_winner,
        ),
        expected=expected,
    )


def preflight(
    plan: TrainingPlan,
    *,
    scorer: Callable[..., Any] | None = None,
    bundle_verifier: Callable[[Path], dict[str, Any]] = verify_bundle,
    expected_initialization: initialization_module.ExpectedHashes | None = None,
) -> Preflight:
    """Metadata-only checks: rows, hashes, R5 composition, environment and budgets.

    ``expected_initialization`` is ``None`` for a real run, which enforces Part A's pinned
    frozen-winner hashes. Only inert fixtures or a future explicit contract set it.
    """
    study = rows_module.load_study(plan.staging, plan.mapping_name)
    initialization = _initialization(plan, expected_initialization)
    receipt = bundle_verifier(plan.model_bundle)
    _check_bundle_agreement(plan)
    output = _output_target(plan.output)
    for protected in (
        plan.staging,
        plan.model_bundle.resolve(),
        plan.init_checkpoint,
        plan.init_delta,
        # The frozen R5 run tree (winner file, its checkpoints and evidence) is immutable.
        plan.r5_winner.parent,
    ):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("r6_output_must_be_separate_from_inputs")
    if plan.recipe_path.is_symlink() or not plan.recipe_path.is_file():
        raise ValueError("r6_recipe_configuration_missing_or_unsafe")
    envelope = _envelope(plan.recipe)
    peak = checkpoint_module.estimated_publication_peak(initialization["trainable_inventory"])
    assert_publication_fits(peak, BudgetPlan.from_recipe(plan.recipe))
    source_inventory = _source_inventory()
    lineage: dict[str, Any] = {
        "recipe": plan.recipe.model_dump(),
        "recipe_hash": recipe_hash(plan.recipe),
        "recipe_config_sha256": digest_file(plan.recipe_path),
        "initialization": initialization,
        "rows": study.lineage(),
        "bundle": receipt,
        "bundle_hash": canonical_hash(receipt),
        "source_files": source_inventory,
        "libraries": _library_versions(),
        "inference": {
            "decoding": plan.recipe.decoding,
            "max_new_tokens": plan.recipe.max_label_tokens,
            "batch_size": evaluate_module.INFERENCE_BATCH_SIZE,
            "seed": plan.recipe.seed,
            "preprocessing": plan.recipe.validation_preprocessing,
            "normalization": evaluate_module.NORMALIZATION,
        },
        "envelope": envelope,
        "publication_peak_bytes": peak,
        "test_access": "not_requested_by_part_b",
    }
    if scorer is not None:
        lineage["scoring_callback"] = _callback_lineage(scorer)
    return Preflight(
        plan=plan,
        study=study,
        initialization=initialization,
        lineage=lineage,
        lineage_hash=canonical_hash(lineage),
        bundle_receipt=receipt,
        bundle_hash=canonical_hash(receipt),
        source_inventory=source_inventory,
        publication_peak_bytes=peak,
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
    digest = evaluate_module.write_predictions(temporary, records)
    temporary.replace(path)
    return digest


def candidate_name(recipe: R6ModelRecipe, pass_index: int) -> str:
    return f"{recipe.candidate}_pass_{pass_index:04d}"


def _candidate_record(
    recipe: R6ModelRecipe,
    commit: checkpoint_module.PassCommit,
    output: Path,
    *,
    lineage_hash: str,
    retained: bool,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": candidate_name(recipe, commit.pass_index),
        "kind": "r6_continuation_pass",
        "lineage_hash": lineage_hash,
        "recipe_hash": recipe_hash(recipe),
        "validation_metrics": {
            "n": commit.n,
            "correct": commit.correct,
            "accuracy": commit.accuracy,
            "scalar_cer": commit.scalar_cer,
            "exact_case_sensitive": True,
        },
        "per_crop_ms_p50": commit.per_crop_ms_p50,
        "predictions_path": str(
            output / f"pass-{commit.pass_index:04d}-validation-predictions-private.jsonl"
        ),
        "predictions_sha256": commit.predictions_sha256,
        "samples": commit.samples,
        "optimizer_steps": commit.optimizer_steps,
        "complete_pass": True,
        "data_epoch": commit.data_epoch,
        "retained": retained,
        "inference": {
            "decoding": recipe.decoding,
            "max_new_tokens": recipe.max_label_tokens,
            "batch_size": evaluate_module.INFERENCE_BATCH_SIZE,
            "preprocessing": recipe.validation_preprocessing,
            "normalization": evaluate_module.NORMALIZATION,
        },
    }
    if retained:
        best = output / "best"
        record["checkpoint_path"] = str(best)
        record["checkpoint_files"] = file_inventory(best)
    return record


def _ranked(
    recipe: R6ModelRecipe,
    commits: Sequence[checkpoint_module.PassCommit],
    output: Path,
    *,
    lineage_hash: str,
    winner: checkpoint_module.PassCommit,
) -> list[dict[str, Any]]:
    records = [
        _candidate_record(
            recipe,
            commit,
            output,
            lineage_hash=lineage_hash,
            retained=commit.pass_index == winner.pass_index,
        )
        for commit in commits
    ]
    return sorted(records, key=rank_key)


def _commit_rank_key(commit: checkpoint_module.PassCommit) -> tuple[float, float, float, int]:
    return (-commit.accuracy, commit.scalar_cer, commit.per_crop_ms_p50, commit.pass_index)


def _winner(commits: Sequence[checkpoint_module.PassCommit]) -> checkpoint_module.PassCommit:
    if not commits:
        raise ValueError("r6_selection_requires_at_least_one_complete_pass")
    if len({item.n for item in commits}) != 1:
        raise ValueError("r6_selection_requires_one_validation_inventory")
    if len({item.pass_index for item in commits}) != len(commits):
        raise ValueError("r6_selection_duplicate_pass_index")
    return min(commits, key=_commit_rank_key)


def _write_results_view(output: Path, commits: Sequence[checkpoint_module.PassCommit]) -> int:
    """Derive the human-readable results view from the committed state."""
    _write_json(
        output / "pass-results.json",
        {
            "version": RESULTS_VERSION,
            "results": [item.model_dump() for item in commits],
        },
    )
    return len(commits)


def _sync_results_view(
    output: Path, commits: Sequence[checkpoint_module.PassCommit]
) -> dict[str, Any]:
    """Rebuild the view after a crash between state publication and view writing."""
    path = output / "pass-results.json"
    observed: list[dict[str, Any]] = []
    if path.is_file() and not path.is_symlink():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") == RESULTS_VERSION:
            observed = payload.get("results", [])
    committed = [item.model_dump() for item in commits]
    if observed == committed:
        return {"view_rebuilt": False, "uncommitted_results_discarded": 0}
    discarded = max(0, len(observed) - len(committed))
    _write_results_view(output, commits)
    return {"view_rebuilt": True, "uncommitted_results_discarded": discarded}


def _checkpoint_commit(
    output: Path,
    *,
    previous_state_digest: str,
    recipe: R6ModelRecipe,
    named: Sequence[tuple[str, Any]],
    lineage: checkpoint_module.StateLineage,
    pass_index: int,
    epoch: int,
    score: Mapping[str, Any],
    observed: Mapping[str, Any],
    predictions_sha256: str,
    training: Mapping[str, Any],
    validation_seconds: float,
) -> checkpoint_module.PassCommit:
    """Write this pass's model-only candidate and return the commit that names it."""
    selected = checkpoint_module.write_selected_candidate(
        output,
        model_named=list(named),
        lineage=lineage,
        pass_index=pass_index,
        data_epoch=epoch,
        source_state_sha256=previous_state_digest,
    )
    return checkpoint_module.PassCommit(
        pass_index=pass_index,
        data_epoch=epoch,
        accuracy=score["accuracy"],
        correct=score["correct"],
        n=score["n"],
        scalar_cer=score["scalar_cer"],
        per_crop_ms_p50=evaluate_module.measured_latency(score, observed),
        checkpoint_sha256=selected.tensors_sha256,
        predictions_sha256=predictions_sha256,
        samples=int(training["samples"]),
        optimizer_steps=int(training["optimizer_steps"]),
        training_seconds=float(training["wall_seconds"]),
        validation_seconds=float(validation_seconds),
    )


def _ensure_best(
    output: Path,
    *,
    recipe: R6ModelRecipe,
    state: checkpoint_module.TrainingState,
    lineage: checkpoint_module.StateLineage,
    named: Sequence[tuple[str, Any]],
) -> tuple[checkpoint_module.PassCommit, str]:
    """Prove or repair the retained winner after an interrupted publication."""
    winner = _winner(state.passes)
    best = output / "best"
    if best.is_dir() and not best.is_symlink():
        _, selected = checkpoint_module.load_selected(best)
        if (
            selected.tensors_sha256 == winner.checkpoint_sha256
            and selected.pass_index == winner.pass_index
        ):
            return winner, "present"
    candidate = output / "selected-candidate"
    if candidate.is_dir() and not candidate.is_symlink():
        _, selected = checkpoint_module.load_selected(candidate)
        if (
            selected.tensors_sha256 == winner.checkpoint_sha256
            and selected.pass_index == winner.pass_index
        ):
            checkpoint_module.promote_selected(output)
            return winner, "recovered_from_candidate"
    if state.pass_index == winner.pass_index:
        checkpoint_module.write_selected_candidate(
            output,
            model_named=list(named),
            lineage=lineage,
            pass_index=winner.pass_index,
            data_epoch=winner.data_epoch,
            source_state_sha256=state.tensors_sha256,
        )
        checkpoint_module.promote_selected(output)
        return winner, "rebuilt_from_latest"
    raise ValueError("r6_committed_winner_checkpoint_cannot_be_restored_in_place")


def _train_pass(
    torch,
    model,
    optimizer,
    processor,
    tokenizer,
    rows: Sequence[Mapping[str, Any]],
    staging: Path,
    recipe: R6ModelRecipe,
    *,
    pass_index: int,
    epoch: int,
    base_step: int,
    total_steps: int,
    output: Path,
    budget: RunBudget,
    progress: Progress,
    candidate: str,
) -> dict[str, Any]:
    parameters_module.set_eval_mode(model)
    trainable = [parameter for parameter in model.parameters() if parameter.requires_grad]
    groups = batching_module.epoch_batches(rows, recipe.trocr_batch_size, recipe.seed, epoch)
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
                            processor, staging, row, recipe, epoch=epoch
                        )
                        for row in group
                    ],
                    dim=0,
                )
                for row in group:
                    if (
                        preprocess_module.parameters_for(recipe, sample_id=row["id"], epoch=epoch)
                        is not None
                    ):
                        augmented += 1
                loss = objective_module.teacher_forced_loss(torch, model, values, labels)
                value = float(loss.detach())
                losses.append(value)
                combined += value / len(accumulation)
                (loss / len(accumulation)).backward()
                seen.extend(row["id"] for row in group)
            torch.nn.utils.clip_grad_norm_(
                trainable, recipe.clip_grad_norm, error_if_nonfinite=True
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
                        "data_epoch": epoch,
                        "loss": combined,
                        "learning_rate": recipe.trocr_learning_rate * multiplier,
                    }
                )
                + "\n"
            )
            progress.event(
                "r6-train",
                candidate=candidate,
                pass_index=pass_index,
                data_epoch=epoch,
                completed_steps=optimizer_steps,
                total_steps=steps_per_pass,
                completed_samples=len(seen),
                total_samples=len(rows),
            )
    batching_module.verify_coverage(rows, groups)
    if len(seen) != len(rows) or len(set(seen)) != len(rows):
        raise ValueError("r6_pass_did_not_use_every_training_row_exactly_once")
    return {
        "pass_index": pass_index,
        "data_epoch": epoch,
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


def _validate_pass(
    torch,
    model,
    tokenizer,
    processor,
    rows: Sequence[Mapping[str, Any]],
    staging: Path,
    recipe: R6ModelRecipe,
    *,
    pass_index: int,
    output: Path,
    scorer: Callable[..., Mapping[str, Any]],
    progress: Progress,
    candidate: str,
) -> tuple[dict[str, Any], dict[str, Any], str, float]:
    started = time.monotonic()
    records, observed = evaluate_module.generate_validation_records(
        torch,
        model,
        tokenizer,
        processor,
        rows,
        staging,
        recipe,
        stage=f"pass-{pass_index:04d}",
        progress=progress,
        candidate=candidate,
    )
    evaluate_module.verify_records(records, rows)
    path = output / f"pass-{pass_index:04d}-validation-predictions-private.jsonl"
    digest = _write_predictions_atomic(path, records)
    score = evaluate_module.score_records(scorer, records, rows)
    _write_json(
        output / f"pass-{pass_index:04d}-score.json",
        {"score": score, "observed": observed},
    )
    return score, observed, digest, time.monotonic() - started


def _budget_plan(recipe: R6ModelRecipe, prior: Consumption) -> BudgetPlan:
    return BudgetPlan.from_recipe(recipe, prior=prior)


def _state_lineage(prepared: Preflight) -> checkpoint_module.StateLineage:
    return checkpoint_module.StateLineage(
        lineage_hash=prepared.lineage_hash,
        recipe_hash=recipe_hash(prepared.recipe),
        base_inventory_sha256=prepared.initialization["base_inventory_sha256"],
        delta_sha256=prepared.initialization["delta_sha256"],
        candidate=prepared.recipe.candidate,
    )


def _load_resume(
    output: Path, *, prepared: Preflight, lineage: checkpoint_module.StateLineage
) -> tuple[checkpoint_module.TrainingState, dict[str, Any], Consumption, dict[str, Any]]:
    """Verify a resumable pass-boundary state and charge any discarded attempt."""
    tensors, state = checkpoint_module.load_state(output / "latest")
    recipe = prepared.recipe
    epochs = [data_epoch(recipe, index) for index in range(1, state.pass_index + 1)]
    prior = checkpoint_module.verify_resume(
        state,
        lineage=lineage,
        trainable_inventory=prepared.initialization["trainable_inventory"],
        pass_limit=recipe.pass_limit,
        data_epochs=epochs,
    )
    charge = 0.0
    marker = output / IN_PROGRESS_MARKER
    if marker.is_file() and not marker.is_symlink():
        charge = float(recipe.interrupted_attempt_charge_seconds)
        marker.unlink()
    prior = Consumption(
        wall_seconds=prior.wall_seconds + charge,
        cpu_seconds=prior.cpu_seconds + charge * CPU_PER_WALL_CONSERVATIVE,
    )
    notes = {
        "resumed_from_pass": state.pass_index,
        "interrupted_attempt_charge_seconds": charge,
        "prior_wall_seconds": prior.wall_seconds,
        "prior_cpu_seconds": prior.cpu_seconds,
    }
    return state, tensors, prior, notes


def run_bounded_training(
    plan: TrainingPlan,
    scorer: Callable[..., Mapping[str, Any]],
    *,
    owner_run: bool,
    runtime: Any | None = None,
    resume: bool = False,
    bundle_verifier: Callable[[Path], dict[str, Any]] = verify_bundle,
    enforce_cpu_limit: bool = True,
    budget_factory: Callable[[BudgetPlan, Path], RunBudget] | None = None,
    progress: Progress | None = None,
    expected_initialization: initialization_module.ExpectedHashes | None = None,
) -> dict[str, Any]:
    """Run at most ``recipe.pass_limit`` complete passes and select on validation only."""
    if not owner_run:
        raise ValueError("r6_model_execution_requires_owner_run_acknowledgment")
    evaluate_module.scorer_arity(scorer)
    sink = progress if progress is not None else Progress()
    prepared = preflight(
        plan,
        scorer=scorer,
        bundle_verifier=bundle_verifier,
        expected_initialization=expected_initialization,
    )
    recipe = prepared.recipe
    output = _output_target(plan.output)
    sink.event(
        "r6-preflight",
        status="ok",
        candidate=recipe.candidate,
        train_samples=len(prepared.study.train),
        validation_samples=len(prepared.study.validation),
        pass_limit=recipe.pass_limit,
        publication_peak_bytes=prepared.publication_peak_bytes,
        lineage_hash=prepared.lineage_hash,
    )
    if resume:
        if output.is_symlink() or not output.is_dir():
            raise ValueError("r6_resume_output_directory_missing")
        if not (output / "latest" / "state.json").is_file():
            raise ValueError("r6_resume_requires_a_pass_complete_state")
        report_path = output / "training-report.json"
        if report_path.is_file():
            existing = json.loads(report_path.read_text(encoding="utf-8"))
            if existing.get("status") == "training_complete_validation_selected":
                raise ValueError("r6_completed_run_must_not_be_resumed_in_place")
    else:
        if output.exists():
            raise ValueError("r6_output_directory_must_be_new")
        output.mkdir(parents=True, mode=0o700)

    if runtime is None:
        from research.english.r6_model.runtime import ContinuationRuntime

        runtime = ContinuationRuntime(plan.model_bundle)

    state_lineage = _state_lineage(prepared)
    state: checkpoint_module.TrainingState | None = None
    tensors: dict[str, Any] | None = None
    prior = Consumption()
    resume_notes: dict[str, Any] = {"requested": resume, "granularity": "pass_boundary_only"}
    if resume:
        state, tensors, prior, resume_extra = _load_resume(
            output, prepared=prepared, lineage=state_lineage
        )
        resume_notes.update(resume_extra)
    budget = (
        RunBudget(_budget_plan(recipe, prior), output)
        if budget_factory is None
        else budget_factory(_budget_plan(recipe, prior), output)
    )
    python_random = random.Random(recipe.seed)
    started_wall, started_cpu = time.monotonic(), time.process_time()
    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "status": "running",
        "stage": "r6_training",
        "candidate": recipe.candidate,
        "task": "letters",
        "capability_status": "research_only",
        "qualified_for_product": False,
        "product_qualification": "none",
        "model_inference": True,
        "test_evaluated": False,
        "final_test": "not_run_by_part_b",
        "lineage_hash": prepared.lineage_hash,
        "lineage": prepared.lineage,
        "initialization": prepared.initialization,
        "python": sys.version,
        "device": "cpu",
        "resume": resume_notes,
        "resources": {"publication_peak_bytes": prepared.publication_peak_bytes},
    }
    try:
        assert_publication_fits(prepared.publication_peak_bytes, budget.plan)
        if enforce_cpu_limit:
            budget.install_cpu_limit()
        budget.start()
        with budget.phase("setup", recipe.max_pass_wall_seconds):
            sink.event("r6-load", status="started", candidate=recipe.candidate)
            torch = runtime.setup(recipe)
            model, tokenizer, inventory = runtime.load_composition(prepared.initialization)
            sink.event(
                "r6-load",
                status="done",
                candidate=recipe.candidate,
                trainable_tensors=len(inventory),
            )
            initialization_module.verify_reconstruction_against_inventory(
                inventory, metadata=prepared.initialization
            )
            named = parameters_module.named_trainable(model)
            optimizer = torch.optim.AdamW(
                [parameter for _, parameter in named],
                lr=recipe.trocr_learning_rate,
                weight_decay=recipe.weight_decay,
            )
            parameters_module.set_eval_mode(model)
            processor = runtime.processor()

        cleaned = checkpoint_module.clean_rotation_leftovers(output)
        if cleaned:
            sink.event(
                "r6-recovery",
                status="reclaimed_rotation_leftovers",
                names=",".join(cleaned),
            )

        passes: list[checkpoint_module.PassCommit] = []
        if state is not None and tensors is not None:
            checkpoint_module.restore_state(model, optimizer, tensors, state)
            checkpoint_module.restore_rng(torch, python_random, state)
            passes = list(state.passes)
            resume_notes.update(_sync_results_view(output, passes))
            sink.event(
                "r6-resume",
                status="restored",
                candidate=recipe.candidate,
                resumed_from_pass=state.pass_index,
                committed_passes=len(passes),
                interrupted_attempt_charge_seconds=resume_notes.get(
                    "interrupted_attempt_charge_seconds", 0.0
                ),
            )
            winner, best_repair = _ensure_best(
                output, recipe=recipe, state=state, lineage=state_lineage, named=named
            )
            resume_notes["selected_checkpoint_state"] = best_repair
            resume_notes["committed_winner_pass"] = winner.pass_index
            checkpoint_module.discard_selected_candidate(output)
        groups_per_pass = math.ceil(len(prepared.study.train) / recipe.trocr_batch_size)
        steps_per_pass = math.ceil(groups_per_pass / recipe.trocr_accumulate_batches)
        planned_total_steps = steps_per_pass * recipe.pass_limit
        first_pass = (state.pass_index + 1) if state is not None else 1
        previous_state_digest = (
            state.tensors_sha256 if state is not None else prepared.initialization["delta_sha256"]
        )
        early_stopped: int | None = None

        for pass_index in range(first_pass, recipe.pass_limit + 1):
            epoch = data_epoch(recipe, pass_index)
            candidate = candidate_name(recipe, pass_index)
            budget.require_wall(
                float(recipe.max_pass_wall_seconds + recipe.max_validation_wall_seconds),
                reason=f"pass-{pass_index}",
            )
            _write_json(
                output / IN_PROGRESS_MARKER,
                {
                    "status": "in_progress",
                    "candidate": candidate,
                    "pass_index": pass_index,
                    "data_epoch": epoch,
                    "prior_wall_seconds": prior.wall_seconds,
                },
            )
            with budget.phase(f"train-{pass_index}", recipe.max_pass_wall_seconds):
                training = _train_pass(
                    torch,
                    model,
                    optimizer,
                    processor=processor,
                    tokenizer=tokenizer,
                    rows=prepared.study.train,
                    staging=prepared.study.staging,
                    recipe=recipe,
                    pass_index=pass_index,
                    epoch=epoch,
                    base_step=(pass_index - 1) * steps_per_pass,
                    total_steps=planned_total_steps,
                    output=output,
                    budget=budget,
                    progress=sink,
                    candidate=candidate,
                )
            with budget.phase(f"validation-{pass_index}", recipe.max_validation_wall_seconds):
                score, observed, predictions_sha, validation_seconds = _validate_pass(
                    torch,
                    model,
                    tokenizer,
                    processor,
                    prepared.study.validation,
                    prepared.study.staging,
                    recipe,
                    pass_index=pass_index,
                    output=output,
                    scorer=scorer,
                    progress=sink,
                    candidate=candidate,
                )
            commit = _checkpoint_commit(
                output,
                previous_state_digest=previous_state_digest,
                recipe=recipe,
                named=named,
                lineage=state_lineage,
                pass_index=pass_index,
                epoch=epoch,
                score=score,
                observed=observed,
                predictions_sha256=predictions_sha,
                training=training,
                validation_seconds=validation_seconds,
            )
            passes.append(commit)
            state = checkpoint_module.publish_latest(
                output,
                model_named=list(named),
                optimizer=optimizer,
                torch=torch,
                python_random=python_random,
                lineage=state_lineage,
                pass_index=pass_index,
                data_epoch=epoch,
                optimizer_step=(state.optimizer_step if state is not None else 0)
                + training["optimizer_steps"],
                passes=passes,
                consumption=budget.consumption(),
            )
            previous_state_digest = state.tensors_sha256
            _write_results_view(output, passes)
            winner = _winner(passes)
            if winner.pass_index == pass_index:
                checkpoint_module.promote_selected(output)
            else:
                checkpoint_module.discard_selected_candidate(output)
            if (output / IN_PROGRESS_MARKER).is_file():
                (output / IN_PROGRESS_MARKER).unlink()
            sink.event(
                "r6-pass",
                status="committed",
                candidate=candidate,
                pass_index=pass_index,
                exact_accuracy=commit.accuracy,
                correct=commit.correct,
                validation_samples=commit.n,
                selected=winner.pass_index == pass_index,
            )
            report.setdefault("passes", []).append(
                {
                    **training,
                    "validation_seconds": validation_seconds,
                    "validation": commit.model_dump(),
                    "observed": observed,
                }
            )
            if recipe.early_stopping_patience and _passes_since_best(passes) >= (
                recipe.early_stopping_patience
            ):
                early_stopped = pass_index
                report["early_stopped_after_pass"] = pass_index
                break

        if state is None or not passes:
            raise ValueError("r6_completed_run_has_no_validation_result")
        winner = _winner(passes)
        ranked = _ranked(
            recipe,
            passes,
            output,
            lineage_hash=prepared.lineage_hash,
            winner=winner,
        )
        best_tensors, best_state = checkpoint_module.load_selected(output / "best")
        if (
            best_state.tensors_sha256 != winner.checkpoint_sha256
            or best_state.pass_index != winner.pass_index
            or best_state.lineage_hash != prepared.lineage_hash
        ):
            raise ValueError("r6_best_checkpoint_differs_from_validation_winner")
        del best_tensors
        selection = {
            "version": SELECTION_VERSION,
            "rule": recipe.checkpoint_selection,
            "candidate": recipe.candidate,
            "winner": ranked[0],
            "ranked_candidates": ranked,
            "validation_samples": winner.n,
            "non_retained_passes": [
                candidate_name(recipe, item.pass_index)
                for item in passes
                if item.pass_index != winner.pass_index
            ],
        }
        _write_json(output / "selection.json", selection)
        _verify_unchanged(prepared, bundle_verifier, expected_initialization)
        retired = False
        if early_stopped is not None or winner.pass_index == recipe.pass_limit:
            retired = checkpoint_module.retire_latest(output)
        report.update(
            status="training_complete_validation_selected",
            selection=selection,
            selected_checkpoint={
                "path": str(output / "best"),
                "name": ranked[0]["name"],
                "pass": winner.pass_index,
                "data_epoch": winner.data_epoch,
                "weights_sha256": best_state.tensors_sha256,
                "source_state_sha256": best_state.source_state_sha256,
                "checkpoint_files": ranked[0]["checkpoint_files"],
            },
            retained_candidate=ranked[0],
            candidates_for_freeze=[ranked[0]],
            pass_results=[item.model_dump() for item in passes],
            training={
                "passes_completed": len(passes),
                "optimizer_steps": state.optimizer_step,
                "samples_per_pass": len(prepared.study.train),
                "validation_samples": len(prepared.study.validation),
                "scheduler": recipe.scheduler,
                "learning_rate": recipe.trocr_learning_rate,
                "augmentation": recipe.augmentation,
                "data_epoch_offset": recipe.data_epoch_offset,
                "optimizer_initialization": recipe.optimizer_initialization,
                "resume_granularity": "pass_boundary_only",
                "selection_rule": recipe.checkpoint_selection,
                "early_stopped_after_pass": early_stopped,
            },
            recovery={"latest_retired_after_final_pass": retired},
            budgets=budget.report(),
            wall_seconds=time.monotonic() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
        )
    except BudgetExceeded as error:
        (output / IN_PROGRESS_MARKER).unlink(missing_ok=True)
        report.update(
            status="incomplete_budget_exceeded",
            reason=str(error),
            selection=None,
            budgets=budget.report(),
            wall_seconds=time.monotonic() - started_wall,
            cpu_seconds=time.process_time() - started_cpu,
            recovered={"resumable_state": (output / "latest" / "state.json").is_file()},
        )
        _write_json(output / "training-report.json", report)
        sink.event(
            "r6-incomplete",
            status="budget_exceeded",
            candidate=recipe.candidate,
            completed_passes=len(report.get("passes", [])),
        )
        return report
    except BaseException as error:
        _write_json(
            output / "failure.json",
            {
                "status": "failed_or_incomplete",
                "exception_type": type(error).__name__,
                "error_detail_private": str(error)[:1000],
                "qualified_for_product": False,
                "resumable_state": (output / "latest" / "state.json").is_file(),
            },
        )
        raise
    finally:
        budget.close()
    _write_json(output / "training-report.json", report)
    sink.event(
        "r6-complete",
        status=report["status"],
        candidate=recipe.candidate,
        passes=len(report.get("pass_results", [])),
        selected_pass=winner.pass_index,
        test_evaluated=False,
    )
    return report


def evaluate_control(
    plan: TrainingPlan,
    scorer: Callable[..., Mapping[str, Any]],
    *,
    owner_run: bool,
    runtime: Any | None = None,
    bundle_verifier: Callable[[Path], dict[str, Any]] = verify_bundle,
    enforce_cpu_limit: bool = True,
    budget_factory: Callable[[BudgetPlan, Path], RunBudget] | None = None,
    progress: Progress | None = None,
    expected_initialization: initialization_module.ExpectedHashes | None = None,
) -> dict[str, Any]:
    """Measure the unchanged R5 winner on the same validation rows, with no training.

    This is the separate control the contract asks for: identical inference settings to
    R5's selected evaluation, no optimizer, no pass, and its own report outside the
    candidate ranking.
    """
    if not owner_run:
        raise ValueError("r6_control_evaluation_requires_owner_run_acknowledgment")
    evaluate_module.scorer_arity(scorer)
    sink = progress if progress is not None else Progress()
    prepared = preflight(
        plan,
        scorer=scorer,
        bundle_verifier=bundle_verifier,
        expected_initialization=expected_initialization,
    )
    recipe = prepared.recipe
    output = _output_target(plan.output)
    control = output / "control"
    if control.exists() or control.is_symlink():
        raise ValueError("r6_control_output_must_be_new")
    budget = (
        RunBudget(_budget_plan(recipe, Consumption()), output)
        if budget_factory is None
        else budget_factory(_budget_plan(recipe, Consumption()), output)
    )
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    started_wall, started_cpu = time.monotonic(), time.process_time()
    sink.event(
        "r6-control",
        status="started",
        candidate=CONTROL_CANDIDATE,
        validation_samples=len(prepared.study.validation),
        lineage_hash=prepared.lineage_hash,
    )
    try:
        if enforce_cpu_limit:
            budget.install_cpu_limit()
        budget.start()
        with budget.phase("control-validation", recipe.max_validation_wall_seconds):
            if runtime is None:
                from research.english.r6_model.runtime import ContinuationRuntime

                runtime = ContinuationRuntime(plan.model_bundle)
            sink.event("r6-load", status="started", candidate=CONTROL_CANDIDATE)
            torch = runtime.setup(recipe)
            model, tokenizer, inventory = runtime.load_composition(prepared.initialization)
            sink.event(
                "r6-load",
                status="done",
                candidate=CONTROL_CANDIDATE,
                trainable_tensors=len(inventory),
            )
            records, observed = evaluate_module.generate_validation_records(
                torch,
                model,
                tokenizer,
                runtime.processor(),
                prepared.study.validation,
                prepared.study.staging,
                recipe,
                stage="control",
                progress=sink,
                candidate=CONTROL_CANDIDATE,
            )
            evaluate_module.verify_records(records, prepared.study.validation)
            control.mkdir(mode=0o700)
            path = control / "validation-predictions-private.jsonl"
            digest = _write_predictions_atomic(path, records)
            score = evaluate_module.score_records(scorer, records, prepared.study.validation)
            value = {
                "version": CONTROL_VERSION,
                "status": "measured_validation_only",
                "candidate": CONTROL_CANDIDATE,
                "kind": "unchanged_r5_selected_composition",
                "lineage_hash": prepared.lineage_hash,
                "initialization": {
                    "policy": prepared.initialization["policy"],
                    "base_inventory_sha256": prepared.initialization["base_inventory_sha256"],
                    "delta_sha256": prepared.initialization["delta_sha256"],
                    "trainable_tensor_count": prepared.initialization["trainable_tensor_count"],
                },
                "validation_metrics": score,
                "per_crop_ms_p50": evaluate_module.measured_latency(score, observed),
                "observed": observed,
                "predictions_path": str(path),
                "predictions_sha256": digest,
                "model_inference": True,
                "training": "not_run",
                "test_evaluated": False,
                "resources": budget.report(),
                "wall_seconds": time.monotonic() - started_wall,
                "cpu_seconds": time.process_time() - started_cpu,
            }
            _write_json(control / "report.json", value)
        sink.event(
            "r6-control",
            status="measured",
            candidate=CONTROL_CANDIDATE,
            exact_accuracy=score["accuracy"],
            correct=score["correct"],
            validation_samples=score["n"],
        )
        return value
    finally:
        budget.close()


def _passes_since_best(passes: Sequence[checkpoint_module.PassCommit]) -> int:
    ranked = min(range(len(passes)), key=lambda index: _commit_rank_key(passes[index]))
    return len(passes) - 1 - ranked


def _verify_unchanged(
    prepared: Preflight,
    bundle_verifier: Callable[[Path], dict[str, Any]],
    expected: initialization_module.ExpectedHashes | None,
) -> None:
    initialization = _initialization(prepared.plan, expected)
    if initialization != prepared.initialization:
        raise ValueError("r6_initialization_changed_during_run")
    if canonical_hash(bundle_verifier(prepared.plan.model_bundle)) != prepared.bundle_hash:
        raise ValueError("r6_model_bundle_changed_during_run")
    if _source_inventory() != prepared.source_inventory:
        raise ValueError("r6_source_code_changed_during_run")
