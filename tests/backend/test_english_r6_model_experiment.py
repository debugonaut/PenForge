"""End-to-end R6 mechanics with a synthetic tiny module and inert fixtures.

No pretrained model, tokenizer, processor, image pipeline or dataset is touched. The module
here is a three-layer 6-wide stack with a tied output projection; it exists only to exercise
real optimizer steps, pass ordering, validation joins, checkpoint publication and resume.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from test_english_r6_model_fixtures import (
    CLASSES,
    VOCAB,
    build_case,
    build_plan,
    build_recipe,
    fake_verifier,
)

from research.english.r5_model.parameters import inventory, named_trainable, select_trainable
from research.english.r6_model import checkpoint, experiment, initialization
from research.english.r6_model.budgets import BudgetExceeded, RunBudget
from research.english.r6_model.initialization import ExpectedHashes
from research.english.r6_model.progress import Progress

TRAIN_SAMPLES = len(CLASSES)


class FakeTokenizer:
    pad_token_id = 1
    eos_token_id = 2
    unk_token_id = 3
    all_special_ids = [0, 1, 2, 3]

    def encode(self, text: str, add_special_tokens: bool = True, truncation: bool = False):
        return [0, 4 + CLASSES.index(text), 2]

    def decode(self, ids, **_):
        for value in ids:
            if 4 <= value < 4 + len(CLASSES):
                return CLASSES[value - 4]
        return ""


class FakeProcessor:
    size = {"height": 384, "width": 384}

    def __call__(self, images, return_tensors="pt", do_resize=True):
        gray = np.asarray(images.convert("L"))
        # The crop is a uniform field carrying the class index; the median survives the
        # bounded affine transform and the pad/resize path.
        value = float(np.median(gray))
        return SimpleNamespace(pixel_values=torch.tensor([[[[value]]]]))


class _InnerDecoder(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.layers = torch.nn.ModuleList([torch.nn.Linear(6, 6) for _ in range(3)])
        self.embed_tokens = torch.nn.Embedding(VOCAB, 6)
        self.embed_positions = torch.nn.Embedding(8, 6)


class _Model(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.decoder = _InnerDecoder()


class _Decoder(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.model = _Model()
        self.output_projection = torch.nn.Linear(6, VOCAB, bias=False)


class TinyTrOCR(torch.nn.Module):
    """Three decoder layers and a tied projection; not a text-recognition model."""

    def __init__(
        self,
        *,
        train_samples: int,
        accuracy_plan: tuple[bool, ...],
        base_passes: int = 0,
    ) -> None:
        super().__init__()
        self.encoder = torch.nn.Linear(1, 1, bias=False)
        self.decoder = _Decoder()
        self.decoder.output_projection.weight = self.decoder.model.decoder.embed_tokens.weight
        self.dropout = torch.nn.Dropout(p=0.2)
        self.forward_calls = 0
        self.train_samples = train_samples
        self.accuracy_plan = accuracy_plan
        # A resumed process only trains the remaining passes; the fixture reports the
        # already committed ones so its synthetic accuracy plan stays pass-aligned.
        self.base_passes = base_passes

    def prepare_decoder_input_ids_from_labels(self, labels):
        shifted = labels.new_zeros(labels.shape)
        shifted[:, 1:] = labels[:, :-1].clone()
        shifted[:, 0] = 0
        shifted.masked_fill_(
            shifted == -100, self.decoder.model.decoder.embed_tokens.num_embeddings - 1
        )
        return shifted

    def forward(self, pixel_values, decoder_input_ids, use_cache=False):
        self.forward_calls += 1
        batch, length = decoder_input_ids.shape
        pooled = pixel_values.reshape(batch, -1).mean(dim=1)
        hidden = self.decoder.model.decoder.embed_tokens(decoder_input_ids.clamp(min=0))
        hidden = hidden + (pooled / 52.0).reshape(batch, 1, 1)
        hidden = self.dropout(torch.tanh(self.decoder.model.decoder.layers[0](hidden)))
        hidden = torch.tanh(self.decoder.model.decoder.layers[1](hidden))
        hidden = torch.tanh(self.decoder.model.decoder.layers[2](hidden))
        return SimpleNamespace(logits=self.decoder.output_projection(hidden))

    def generate(
        self,
        pixel_values,
        do_sample=False,
        num_beams=1,
        max_new_tokens=8,
        use_cache=True,
    ):
        batch = pixel_values.shape[0]
        indexes = [
            int(round(float(value))) for value in pixel_values.reshape(batch, -1).mean(dim=1)
        ]
        # One forward per training sample, so the completed-pass index is calls-1 // n.
        level = min(
            self.base_passes + max(0, (self.forward_calls - 1) // max(1, self.train_samples)),
            len(self.accuracy_plan) - 1,
        )
        correct = self.accuracy_plan[level]
        rows = []
        for index in indexes:
            predicted = index if correct else (index + 1) % len(CLASSES)
            rows.append([0, 4 + predicted, 2])
        return torch.tensor(rows, dtype=torch.long)


class FakeRuntime:
    def __init__(
        self, *, accuracy_plan: tuple[bool, ...] = (True,), output: Path | None = None
    ) -> None:
        self.accuracy_plan = accuracy_plan
        self.output = output
        self.loads = 0

    def committed_passes(self) -> int:
        if self.output is None:
            return 0
        path = self.output / "pass-results.json"
        if not path.is_file() or path.is_symlink():
            return 0
        return len(json.loads(path.read_text()).get("results", []))

    def setup(self, recipe):
        random.seed(recipe.seed)
        torch.manual_seed(recipe.seed)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        torch.set_num_threads(2)
        torch.use_deterministic_algorithms(True)
        return torch

    def tokenizer(self):
        return FakeTokenizer()

    def processor(self):
        return FakeProcessor()

    def load_composition(self, initialization_metadata, *, torch=None):
        from safetensors.numpy import load_file

        if torch is None:
            import torch as torch_module

            torch = torch_module
        self.loads += 1
        model = TinyTrOCR(
            train_samples=TRAIN_SAMPLES,
            accuracy_plan=self.accuracy_plan,
            base_passes=self.committed_passes(),
        )
        base = load_file(
            str(Path(initialization_metadata["base_checkpoint"]) / "model.safetensors")
        )
        with torch.no_grad():
            for name, array in base.items():
                model.get_parameter(name).copy_(torch.from_numpy(array))
        select_trainable(model)
        observed = inventory(named_trainable(model))
        delta = load_file(
            str(Path(initialization_metadata["delta_checkpoint"]) / "state.safetensors")
        )
        tensors = {name: torch.from_numpy(array) for name, array in delta.items()}
        initialization.apply_delta(torch, model, tensors, observed)
        return model, FakeTokenizer(), observed


class TripBeforeNextPassBudget(RunBudget):
    """Simulate a budget wall that only allows the first complete pass."""

    def __init__(self, plan, output, *, trip_at: int = 2) -> None:
        super().__init__(plan, output)
        self.calls = 0
        self.trip_at = trip_at

    def require_wall(self, seconds: float, *, reason: str) -> None:
        self.calls += 1
        if self.calls >= self.trip_at:
            raise BudgetExceeded("aggregate_wall_ceiling_insufficient:simulated")
        super().require_wall(seconds, reason=reason)


def expectations(case) -> ExpectedHashes:
    return ExpectedHashes(
        winner_sha256=case["expected_winner_sha256"],
        delta_sha256=case["expected_delta_sha256"],
    )


def scorer_factory():
    calls = {"n": 0}

    def scorer(records):
        calls["n"] += 1
        correct = sum(1 for record in records if record.prediction == record.truth)
        n = len(records)
        return {
            "accuracy": correct / n,
            "correct": correct,
            "n": n,
            "scalar_cer": 1.0 - correct / n,
            "exact_case_sensitive": True,
        }

    return scorer, calls


def run(case, output, *, runtime, scorer, resume=False, budget_factory=None, lines=None, **extra):
    plan = build_plan(case, output)
    if getattr(runtime, "output", None) is None:
        runtime.output = output
    return experiment.run_bounded_training(
        plan,
        scorer,
        owner_run=True,
        runtime=runtime,
        resume=resume,
        bundle_verifier=fake_verifier,
        enforce_cpu_limit=False,
        budget_factory=budget_factory,
        progress=Progress(writer=(lines.append if lines is not None else (lambda line: None))),
        expected_initialization=extra.get("expected", expectations(case)),
    )


def test_preflight_reports_verified_inputs_without_creating_output(tmp_path):
    case = build_case(tmp_path / "case")
    plan = build_plan(case, tmp_path / "run")
    prepared = experiment.preflight(
        plan,
        bundle_verifier=fake_verifier,
        expected_initialization=expectations(case),
    )
    assert len(prepared.study.train) == len(prepared.study.validation) == 52
    assert prepared.initialization["trainable_tensor_count"] == 5
    assert prepared.lineage["test_access"] == "not_requested_by_part_b"
    assert prepared.lineage["envelope"]["pass_limit"] == 2
    assert prepared.publication_peak_bytes < plan.recipe.max_output_bytes
    assert not (tmp_path / "run").exists()


def test_preflight_rejects_a_test_row_at_the_package_boundary(tmp_path):
    case = build_case(tmp_path / "case")
    mapping = case["staging"] / "train-validation.jsonl"
    rows = [json.loads(line) for line in mapping.read_text().splitlines()]
    leak = dict(rows[0])
    leak["id"] = "r6-leak"
    leak["split"] = "test"
    mapping.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in [*rows, leak]),
        encoding="utf-8",
    )
    plan = build_plan(case, tmp_path / "run")
    with pytest.raises(ValueError):
        experiment.preflight(
            plan,
            bundle_verifier=fake_verifier,
            expected_initialization=expectations(case),
        )


def test_bounded_run_commits_two_complete_passes_and_selects_on_validation(tmp_path):
    case = build_case(tmp_path / "case")
    output = tmp_path / "run"
    scorer, calls = scorer_factory()
    lines: list[str] = []
    report = run(
        case,
        output,
        runtime=FakeRuntime(accuracy_plan=(False, True)),
        scorer=scorer,
        lines=lines,
    )
    assert report["status"] == "training_complete_validation_selected"
    assert report["test_evaluated"] is False
    assert report["final_test"] == "not_run_by_part_b"
    assert report["training"]["passes_completed"] == 2
    assert report["training"]["optimizer_steps"] == 26  # 13 accumulation steps per pass
    assert report["training"]["samples_per_pass"] == 52
    assert [item["pass_index"] for item in report["pass_results"]] == [1, 2]
    assert [item["correct"] for item in report["pass_results"]] == [0, 52]
    winner = report["selection"]["winner"]
    assert winner["name"] == "r6_continuation_r5_pass3_pass_0002"
    assert winner["validation_metrics"]["accuracy"] == 1.0
    assert winner["retained"] is True
    assert report["selection"]["non_retained_passes"] == ["r6_continuation_r5_pass3_pass_0001"]
    assert calls["n"] == 2
    retained = report["retained_candidate"]
    assert retained["checkpoint_path"] == str(output / "best")
    assert sorted(retained["checkpoint_files"]) == ["state.json", "state.safetensors"]
    assert retained["predictions_sha256"] == report["pass_results"][1]["predictions_sha256"]
    assert report["candidates_for_freeze"] == [retained]
    selection = json.loads((output / "selection.json").read_text())
    assert selection["rule"] == (
        "validation_exact_accuracy_then_scalar_cer_then_measured_latency_then_candidate_id"
    )
    assert len(selection["ranked_candidates"]) == 2
    # The winner is retained; the run is finished, so the optimizer state is retired.
    assert (output / "best" / "state.json").is_file()
    assert report["recovery"]["latest_retired_after_final_pass"] is True
    assert not (output / "latest").exists()
    assert not (output / "selected-candidate").exists()
    assert not (output / "latest.old").exists()
    for name in ("pass-results.json", "training-report.json", "selection.json"):
        assert (output / name).is_file()
    predictions = output / "pass-0002-validation-predictions-private.jsonl"
    assert predictions.is_file()
    state = json.loads((output / "best" / "state.json").read_text())
    assert state["status"] == "pass_complete" and state["weights_only"] is True
    assert state["candidate"] == "r6_continuation_r5_pass3"
    assert len(state["trainable_inventory"]) == 5
    # Progress is flushed JSON with counts only.
    events = [json.loads(line) for line in lines]
    stages = [event["stage"] for event in events]
    assert "r6-preflight" in stages and "r6-load" in stages and "r6-train" in stages
    assert "r6-pass" in stages and "r6-complete" in stages
    train_events = [event for event in events if event["stage"] == "r6-train"]
    assert train_events[-1]["completed_steps"] == 13
    assert train_events[-1]["completed_samples"] == 52
    assert all("label" not in event and "prediction" not in event for event in events)


def test_resume_reproduces_the_uninterrupted_run(tmp_path):
    case = build_case(tmp_path / "case")
    baseline_output = tmp_path / "baseline"
    scorer, _ = scorer_factory()
    baseline = run(
        case,
        baseline_output,
        runtime=FakeRuntime(accuracy_plan=(False, True)),
        scorer=scorer,
    )
    assert baseline["status"] == "training_complete_validation_selected"

    interrupted_output = tmp_path / "interrupted"
    scorer, _ = scorer_factory()
    first = run(
        case,
        interrupted_output,
        runtime=FakeRuntime(accuracy_plan=(False, True)),
        scorer=scorer,
        budget_factory=lambda plan, output: TripBeforeNextPassBudget(plan, output),
    )
    assert first["status"] == "incomplete_budget_exceeded"
    assert first["selection"] is None
    assert first["recovered"]["resumable_state"] is True
    assert (interrupted_output / "latest" / "state.json").is_file()
    assert len(json.loads((interrupted_output / "pass-results.json").read_text())["results"]) == 1

    scorer, _ = scorer_factory()
    resumed = run(
        case,
        interrupted_output,
        runtime=FakeRuntime(accuracy_plan=(False, True)),
        scorer=scorer,
        resume=True,
    )
    assert resumed["status"] == "training_complete_validation_selected"
    assert resumed["resume"]["resumed_from_pass"] == 1
    assert resumed["resume"]["view_rebuilt"] is False
    assert resumed["training"]["optimizer_steps"] == baseline["training"]["optimizer_steps"]
    baseline_state = json.loads((baseline_output / "best" / "state.json").read_text())
    resumed_state = json.loads((interrupted_output / "best" / "state.json").read_text())
    assert resumed_state["tensors_sha256"] == baseline_state["tensors_sha256"]
    assert resumed_state["pass_index"] == baseline_state["pass_index"] == 2
    assert [item["predictions_sha256"] for item in resumed["pass_results"]] == [
        item["predictions_sha256"] for item in baseline["pass_results"]
    ]
    assert [item["correct"] for item in resumed["pass_results"]] == [0, 52]


def test_resume_rejects_a_changed_recipe(tmp_path):
    case = build_case(tmp_path / "case")
    scorer, _ = scorer_factory()
    output = tmp_path / "run"
    run(
        case,
        output,
        runtime=FakeRuntime(accuracy_plan=(False, True)),
        scorer=scorer,
        budget_factory=lambda plan, path: TripBeforeNextPassBudget(plan, path),
    )
    changed = build_recipe(case["root"], pass_limit=2, name="changed-recipe.json")
    raw = json.loads(changed.read_text())
    raw["data_epoch_offset"] = 9
    changed.write_text(json.dumps(raw), encoding="utf-8")
    plan = build_plan(case, output, recipe=changed)
    runtime = FakeRuntime(accuracy_plan=(False, True), output=output)
    with pytest.raises(ValueError, match="r6_resume_lineage_changed"):
        experiment.run_bounded_training(
            plan,
            scorer,
            owner_run=True,
            runtime=runtime,
            resume=True,
            bundle_verifier=fake_verifier,
            enforce_cpu_limit=False,
            budget_factory=lambda plan, path: RunBudget(plan, path),
            progress=Progress(writer=lambda line: None),
            expected_initialization=expectations(case),
        )


def test_resume_charges_a_discarded_attempt_conservatively(tmp_path):
    case = build_case(tmp_path / "case")
    output = tmp_path / "run"
    scorer, _ = scorer_factory()
    run(
        case,
        output,
        runtime=FakeRuntime(accuracy_plan=(False, True)),
        scorer=scorer,
        budget_factory=lambda plan, path: TripBeforeNextPassBudget(plan, path),
    )
    state = json.loads((output / "latest" / "state.json").read_text())
    # A crash after the pass marker was written but before the pass committed.
    marker = {"status": "in_progress", "pass_index": 2}
    (output / "pass-in-progress.json").write_text(json.dumps(marker), encoding="utf-8")
    scorer, _ = scorer_factory()
    resumed = run(
        case,
        output,
        runtime=FakeRuntime(accuracy_plan=(False, True)),
        scorer=scorer,
        resume=True,
    )
    assert resumed["resume"]["interrupted_attempt_charge_seconds"] == 120
    assert resumed["resume"]["prior_wall_seconds"] == pytest.approx(
        state["budget"]["wall_seconds"] + 120
    )
    assert resumed["resume"]["prior_cpu_seconds"] == pytest.approx(
        state["budget"]["cpu_seconds"] + 180
    )
    assert not (output / "pass-in-progress.json").exists()
    assert resumed["status"] == "training_complete_validation_selected"


def test_early_stopping_stops_after_a_non_improving_complete_pass(tmp_path):
    case = build_case(tmp_path / "case", pass_limit=3)
    output = tmp_path / "run"
    scorer, calls = scorer_factory()
    report = run(
        case,
        output,
        runtime=FakeRuntime(accuracy_plan=(True, False, True)),
        scorer=scorer,
    )
    assert report["status"] == "training_complete_validation_selected"
    assert report["training"]["passes_completed"] == 2
    assert report["early_stopped_after_pass"] == 2
    assert report["selection"]["winner"]["name"] == "r6_continuation_r5_pass3_pass_0001"
    assert [item["correct"] for item in report["pass_results"]] == [52, 0]
    assert calls["n"] == 2
    assert report["recovery"]["latest_retired_after_final_pass"] is True


def test_budget_failure_never_selects_a_partial_or_truncated_pass(tmp_path):
    case = build_case(tmp_path / "case")
    output = tmp_path / "run"
    scorer, _ = scorer_factory()
    lines: list[str] = []
    report = run(
        case,
        output,
        runtime=FakeRuntime(accuracy_plan=(True, True)),
        scorer=scorer,
        budget_factory=lambda plan, path: TripBeforeNextPassBudget(plan, path),
        lines=lines,
    )
    assert report["status"] == "incomplete_budget_exceeded"
    assert report["selection"] is None
    assert "pass_results" not in report or len(report.get("pass_results", [])) <= 1
    assert json.loads((output / "training-report.json").read_text())["status"] == (
        "incomplete_budget_exceeded"
    )
    results = json.loads((output / "pass-results.json").read_text())["results"]
    assert [item["pass_index"] for item in results] == [1]
    assert (output / "best" / "state.json").is_file()
    assert any(json.loads(line)["stage"] == "r6-incomplete" for line in lines)


def test_output_ceiling_is_enforced_cooperatively(tmp_path):
    case = build_case(tmp_path / "case")
    plan = build_plan(case, tmp_path / "run")
    scorer, _ = scorer_factory()
    report = experiment.run_bounded_training(
        plan,
        scorer,
        owner_run=True,
        runtime=FakeRuntime(),
        bundle_verifier=fake_verifier,
        enforce_cpu_limit=False,
        budget_factory=lambda budget, output: RunBudget(
            budget, output, disk_bytes=lambda path: budget.output_bytes + 1
        ),
        progress=Progress(writer=lambda line: None),
        expected_initialization=expectations(case),
    )
    assert report["status"] == "incomplete_budget_exceeded"
    assert "output_ceiling_exceeded" in report["reason"]
    assert report["selection"] is None


def test_control_is_measured_separately_and_never_trained(tmp_path):
    case = build_case(tmp_path / "case")
    output = tmp_path / "run"
    plan = build_plan(case, output)
    scorer, _ = scorer_factory()
    runtime = FakeRuntime(accuracy_plan=(True,))
    value = experiment.evaluate_control(
        plan,
        scorer,
        owner_run=True,
        runtime=runtime,
        bundle_verifier=fake_verifier,
        enforce_cpu_limit=False,
        progress=Progress(writer=lambda line: None),
        expected_initialization=expectations(case),
    )
    assert value["status"] == "measured_validation_only"
    assert value["candidate"] == "r5_unchanged_control"
    assert value["training"] == "not_run"
    assert value["test_evaluated"] is False
    assert value["validation_metrics"]["n"] == 52
    assert value["validation_metrics"]["accuracy"] == 1.0
    assert Path(value["predictions_path"]).is_file()
    assert runtime.loads == 1
    assert not (output / "best").exists()
    with pytest.raises(ValueError, match="control_output_must_be_new"):
        experiment.evaluate_control(
            plan,
            scorer,
            owner_run=True,
            runtime=runtime,
            bundle_verifier=fake_verifier,
            enforce_cpu_limit=False,
            progress=Progress(writer=lambda line: None),
            expected_initialization=expectations(case),
        )


def test_owner_run_acknowledgment_is_required(tmp_path):
    case = build_case(tmp_path / "case")
    plan = build_plan(case, tmp_path / "run")
    scorer, _ = scorer_factory()
    with pytest.raises(ValueError, match="owner_run"):
        experiment.run_bounded_training(plan, scorer, owner_run=False, runtime=FakeRuntime())
    with pytest.raises(ValueError, match="owner_run"):
        experiment.evaluate_control(plan, scorer, owner_run=False, runtime=FakeRuntime())


def test_scorer_must_report_exact_case_sensitive_counts(tmp_path):
    case = build_case(tmp_path / "case")
    output = tmp_path / "run"
    plan = build_plan(case, output)

    def broken(records):
        return {
            "accuracy": 0.5,
            "correct": 3,
            "n": len(records),
            "scalar_cer": 0.5,
            "exact_case_sensitive": False,
        }

    with pytest.raises(ValueError, match="exact_case_sensitive"):
        experiment.run_bounded_training(
            plan,
            broken,
            owner_run=True,
            runtime=FakeRuntime(),
            bundle_verifier=fake_verifier,
            enforce_cpu_limit=False,
            progress=Progress(writer=lambda line: None),
            expected_initialization=expectations(case),
        )
    assert (output / "failure.json").is_file()
    failure = json.loads((output / "failure.json").read_text())
    assert failure["status"] == "failed_or_incomplete"
    assert "label" not in failure and "prediction" not in failure
    assert checkpoint.STATE_FILES == ("state.safetensors", "state.json")
