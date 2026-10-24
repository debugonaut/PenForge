"""Inert lifecycle tests. No pretrained weights, corpus inference or fitting."""

import json
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from research.english.r6_eval import execution, snapshot


def test_missing_part_b_handoff_refuses_before_import_or_run_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="handoff_incomplete"):
        execution.part_b_preflight({})
    assert not list(tmp_path.iterdir())


def test_metadata_context_rejects_framework_import_in_fresh_process():
    code = """
import sys
from research.english.r6_eval.execution import metadata_only
try:
    with metadata_only():
        import torch
except ValueError as e:
    assert "must_not_import" in str(e)
else:
    raise AssertionError("framework import escaped metadata guard")
assert "torch" not in sys.modules
assert "transformers" not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_data_context_rejects_r5_output_overlap_before_reading_inputs(tmp_path):
    with pytest.raises(ValueError, match="separate_from_inputs"):
        execution.data_context(
            tmp_path / "stage", tmp_path / "bundle", tmp_path / "old-run", tmp_path / "old-run/new"
        )


def test_preflight_pins_session_and_refuses_drift_or_collision(tmp_path, monkeypatch):
    run = tmp_path / "run"
    ctx = {"run": run}
    monkeypatch.setattr(execution, "part_b_preflight", lambda ctx: {"fixture": True})
    current = {"hash": "one"}
    monkeypatch.setattr(execution, "lineage", lambda ctx, model: dict(current))
    value = execution.preflight(ctx)
    assert value["model_execution"] is False
    assert execution.preflight(ctx) == value
    current["hash"] = "two"
    with pytest.raises(ValueError, match="session_source_data_environment_or_recipe_changed"):
        execution.preflight(ctx)
    other = tmp_path / "collision"
    other.mkdir()
    (other / "unrelated").write_text("preserve")
    with pytest.raises(ValueError, match="directory_collision"):
        execution.preflight({"run": other})
    assert (other / "unrelated").read_text() == "preserve"


def source_fixture(root):
    files = {
        "pyproject.toml": "project",
        "uv.lock": "locked",
        "scripts/train_english_r5_research.sh": "old wrapper",
        "scripts/train_english_r6_research.sh": "new wrapper",
        "research/ENGLISH_R5_MODEL.md": "historical",
        "research/ENGLISH_R6_MODEL.md": "complete",
        "research/ENGLISH_R6_CONTRACT.md": "contract",
        "research/english/r6_model/example.py": "audited source",
        "research/runs/private.json": "private",
        ".env": "private",
        "research/data/a.png": "private",
    }
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)


def test_source_freeze_requires_readiness_restores_and_rejects_cache_drift(tmp_path):
    origin, run = tmp_path / "origin", tmp_path / "run"
    source_fixture(origin)
    run.mkdir()
    with pytest.raises(ValueError, match="readiness"):
        snapshot.freeze(run, source_root=origin)
    value = snapshot.freeze(run, source_root=origin, ready=True)
    target, _ = snapshot.verify(run)
    assert len(value["files"]) == 8
    assert not (target / "research/runs/private.json").exists()
    assert not (target / ".env").exists()
    (origin / "research/english/r6_model/example.py").write_text("concurrent edit")
    assert (target / "research/english/r6_model/example.py").read_text() == "audited source"
    shutil.rmtree(target)
    assert snapshot.verify(run)[0] == target
    (target / "unexpected.py").write_text("drift")
    with pytest.raises(ValueError, match="cache_drift"):
        snapshot.verify(run)
    (target / "unexpected.py").unlink()
    archive = run / "execution-source.zip"
    archive.write_bytes(b"drift")
    with pytest.raises(ValueError, match="archive_changed"):
        snapshot.verify(run)
    shutil.rmtree(target)


def fake_training_context(tmp_path):
    run = tmp_path / "run"
    (run / "control").mkdir(parents=True)
    (run / "control/report.json").write_text("{}")
    (run / "session.json").write_text("{}")
    ctx = {"run": run, "data": {"inputs": SimpleNamespace(score_validation=lambda records: {})}}
    recipe = SimpleNamespace(max_run_wall_seconds=11700, max_cpu_seconds=22000, pass_limit=2)
    return ctx, SimpleNamespace(recipe=recipe)


def test_soft_interruption_retains_claim_requires_explicit_resume_and_charges_usage(
    tmp_path, monkeypatch
):
    ctx, plan = fake_training_context(tmp_path)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()

    model = {"module": SimpleNamespace(run_bounded_training=interrupted), "plan": plan}
    monkeypatch.setattr(execution, "verified_control", lambda ctx: {})
    monkeypatch.setattr(execution, "budget_factory", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="owner_run"):
        execution.train(ctx, model)
    with pytest.raises(KeyboardInterrupt):
        execution.train(ctx, model, owner_run=True)
    claims = list((ctx["run"] / "attempts").glob("attempt-*.json"))
    assert len(claims) == 1
    outcome = json.loads((ctx["run"] / "attempts/outcome-0001.json").read_text())
    assert outcome["exception"] == "KeyboardInterrupt"
    assert outcome["wall_seconds"] >= 0
    with pytest.raises(ValueError, match="explicit_resume"):
        execution.train(ctx, model, owner_run=True)
    prior = execution.previous_consumption(ctx["run"] / "attempts", claims)
    assert prior[0] >= 0 and prior[1] >= 0
    model["module"].run_bounded_training = lambda *args, **kwargs: {
        "status": "training_complete_validation_selected"
    }
    assert (
        execution.train(ctx, model, owner_run=True, resume=True)["status"]
        == "training_complete_validation_selected"
    )
    assert len(list((ctx["run"] / "attempts").glob("attempt-*.json"))) == 2
    with pytest.raises(ValueError, match="pass_and_replay_allowance"):
        execution.train(ctx, model, owner_run=True, resume=True)


def test_hard_interruption_cannot_reset_or_guess_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "verified_control", lambda ctx: {})
    ctx, plan = fake_training_context(tmp_path)
    attempts = ctx["run"] / "attempts"
    attempts.mkdir()
    (attempts / "attempt-0001.json").write_text(
        json.dumps(
            {
                "status": "claimed_before_model_load",
                "session_sha256": __import__("hashlib")
                .sha256((ctx["run"] / "session.json").read_bytes())
                .hexdigest(),
            }
        )
    )
    model = {"plan": plan}
    with pytest.raises(ValueError, match="unmeasured_interrupted_attempt"):
        execution.train(ctx, model, owner_run=True, resume=True)
    assert len(list(attempts.glob("attempt-*.json"))) == 1


@pytest.mark.parametrize("bad", [None, -1, float("inf"), True])
def test_corrupt_consumption_does_not_enable_resume(tmp_path, bad):
    (tmp_path / "attempt-0001.json").write_text("{}")
    (tmp_path / "outcome-0001.json").write_text(json.dumps({"wall_seconds": bad, "cpu_seconds": 0}))
    with pytest.raises(ValueError, match="consumption_missing_or_invalid"):
        execution.previous_consumption(tmp_path, list(tmp_path.glob("attempt-*.json")))


def test_budget_factory_retains_tighter_native_limits_and_counts_entire_run(tmp_path):
    from research.english.r6_model.budgets import BudgetPlan, Consumption

    plan = BudgetPlan(3600, 1200, 11700, 22000, 6000, 2600000000, Consumption(100, 200))
    guard = execution.budget_factory({"run": tmp_path}, prior_wall=400, prior_cpu=500)(
        plan, tmp_path / "training"
    )
    assert guard.output == tmp_path
    assert guard.plan.prior == Consumption(400, 500)
    assert guard.plan.run_wall_seconds == 11700
    assert guard.plan.output_bytes == 2600000000
    assert guard.plan.cpu_seconds == 22000
    guard.close()


@pytest.mark.parametrize(
    "stage",
    ["prepare", "preflight", "freeze-source", "control", "train", "resume", "select", "final-test"],
)
def test_wrapper_dispatch_is_explicit_and_owner_flags_are_narrow(tmp_path, stage):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    path = scripts / "train_english_r6_research.sh"
    shutil.copyfile("scripts/train_english_r6_research.sh", path)
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    python.chmod(0o700)
    result = subprocess.run(["bash", str(path), stage], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    assert ("--owner-run" in args) == (stage in {"train", "resume"})
    assert args.count(stage) == 1


def test_final_command_refuses_before_reading_real_inputs_or_creating_claims(tmp_path):
    arguments = [sys.executable, "-m", "research.english.r6_run", "final-test"]
    for key in ("staging", "model", "r5-run", "run"):
        arguments.extend(["--" + key, str(tmp_path / key)])
    result = subprocess.run(arguments, capture_output=True, text=True)
    assert result.returncode == 2
    assert "fresh_final_unavailable" in result.stderr
    assert not list(tmp_path.iterdir())


def test_output_budget_derivation_counts_both_selected_states_without_changing_hyperparameters(
    tmp_path,
):
    source = tmp_path / "model.json"
    native = {"max_output_bytes": 2600000000, "trocr_learning_rate": 0.000008, "pass_limit": 2}
    source.write_text(json.dumps(native))
    # The real verified trainable byte count, represented by an inert shape only.
    ctx = {
        "run": tmp_path / "run",
        "initialization_metadata": {
            "trainable_inventory": {"w": {"shape": [84016128], "dtype": "float32"}}
        },
    }
    path, receipt = execution.effective_recipe(ctx, source)
    actual = json.loads(path.read_text())
    assert 2688000000 < receipt["conservative_publication_peak_bytes"] < 2700000000
    assert receipt["effective_output_bytes"] == 3000000000
    assert actual == {**native, "max_output_bytes": 3000000000}
    assert json.loads(source.read_text()) == native
    assert execution.effective_recipe(ctx, source) == (path, receipt)
    actual["pass_limit"] = 3
    path.write_text(json.dumps(actual))
    with pytest.raises(ValueError, match="receipt_drift"):
        execution.effective_recipe(ctx, source)


def test_budget_derivation_refuses_publication_above_existing_owner_ceiling(tmp_path):
    source = tmp_path / "model.json"
    source.write_text('{"max_output_bytes":3000000000}')
    ctx = {
        "run": tmp_path / "run",
        "initialization_metadata": {
            "trainable_inventory": {"w": {"shape": [100000000], "dtype": "float32"}}
        },
    }
    with pytest.raises(ValueError, match="owner_output_ceiling"):
        execution.effective_recipe(ctx, source)
    assert not ctx["run"].exists()


def selection_fixture(tmp_path, *, adapted_wins):
    import struct

    from research.english.r5_data.common import digest_file
    from research.english.r5_eval.lifecycle import file_inventory
    from research.english.r6_eval.scoring import score_predictions

    run = tmp_path / "run"
    (run / "control").mkdir(parents=True)
    (run / "training/best").mkdir(parents=True)
    rows = [
        {"id": "one", "label": "A", "writer": "w1", "split": "validation"},
        {"id": "two", "label": "a", "writer": "w2", "split": "validation"},
    ]

    def records(correct):
        return [
            {
                "sample_id": r["id"],
                "truth": r["label"],
                "prediction": r["label"] if i < correct else "?",
            }
            for i, r in enumerate(rows)
        ]

    def write_records(path, data):
        path.write_text("".join(json.dumps(r) + "\n" for r in data))

    control_records = records(0 if adapted_wins else 2)
    control_prediction = run / "control/validation-predictions-private.jsonl"
    write_records(control_prediction, control_records)
    control_checkpoint = tmp_path / "old-checkpoint"
    control_checkpoint.mkdir()
    (control_checkpoint / "inert.txt").write_text("old")
    baseline = {
        "name": "r5_unchanged_control",
        "kind": "r5_trainable_delta",
        "checkpoint_path": str(control_checkpoint),
        "checkpoint_files": file_inventory(control_checkpoint),
        "predictions_path": str(control_prediction),
        "predictions_sha256": digest_file(control_prediction),
        "validation_metrics": score_predictions(rows, control_records),
        "per_crop_ms_p50": 9.0,
        "inference_recipe": {"seed": 20261023},
    }
    (run / "control/report.json").write_text(json.dumps(baseline))
    inventory = {"w": {"shape": [1], "dtype": "float32"}}
    header = json.dumps(
        {"model.w": {"shape": [1], "dtype": "F32", "data_offsets": [0, 4]}}
    ).encode()
    tensor = run / "training/best/state.safetensors"
    tensor.write_bytes(struct.pack("<Q", len(header)) + header + b"\0" * 4)
    state = {
        "version": "english-r6-selected-state-v2",
        "status": "pass_complete",
        "weights_only": True,
        "pass_index": 2,
        "lineage_hash": "fixture",
        "tensors_sha256": digest_file(tensor),
        "trainable_inventory": inventory,
    }
    (run / "training/best/state.json").write_text(json.dumps(state))
    results = []
    for index, correct in ((1, 0), (2, 1)):
        path = run / f"training/pass-{index:04d}-validation-predictions-private.jsonl"
        data = records(correct)
        write_records(path, data)
        score = score_predictions(rows, data)
        results.append(
            {
                **{k: score[k] for k in ("correct", "n", "accuracy", "scalar_cer")},
                "pass_index": index,
                "samples": 6656,
                "optimizer_steps": 1664,
                "predictions_sha256": digest_file(path),
                "per_crop_ms_p50": 10.0,
                "checkpoint_sha256": digest_file(tensor),
            }
        )
    retained = {
        "name": "r6_fixture_pass_0002",
        "complete_pass": True,
        "samples": 6656,
        "predictions_sha256": results[-1]["predictions_sha256"],
        "checkpoint_files": file_inventory(run / "training/best"),
        "inference": {"decoding": "greedy"},
    }
    report = {
        "status": "training_complete_validation_selected",
        "test_evaluated": False,
        "lineage_hash": "fixture",
        "candidate": "r6_fixture",
        "pass_results": results,
        "passes": [],
        "training": {"passes_completed": 2},
        "retained_candidate": retained,
        "resume": {"requested": True},
    }
    (run / "training/training-report.json").write_text(json.dumps(report))
    (run / "session.json").write_text("{}")
    (run / "execution-source.json").write_text("{}")
    ctx = {
        "run": run,
        "data": {"rows": {"validation": rows}},
        "initialization_metadata": {"trainable_inventory": inventory},
    }
    prepared = SimpleNamespace(lineage_hash="fixture", recipe=SimpleNamespace(seed=20261023))
    return ctx, prepared, baseline


@pytest.mark.parametrize("adapted_wins", [False, True])
def test_selection_rescores_all_candidates_handles_resume_commits_and_keeps_control(
    tmp_path, monkeypatch, adapted_wins
):
    from research.english.r6_eval.lifecycle import verify_frozen

    ctx, prepared, baseline = selection_fixture(tmp_path, adapted_wins=adapted_wins)
    monkeypatch.setattr(execution, "verified_control", lambda ctx: baseline)
    value = execution.freeze_selection(ctx, prepared)
    expected = "r6_fixture_pass_0002" if adapted_wins else "r5_unchanged_control"
    assert value["winner"]["name"] == expected
    assert value["fresh_final"].startswith("unavailable")
    assert value["test_evaluated"] is False
    assert verify_frozen(ctx["run"] / "winner-frozen.json", {}) == value
    with pytest.raises((FileExistsError, ValueError)):
        execution.freeze_selection(ctx, prepared)
    assert value["winner"]["name"] == expected


def test_selection_rejects_prediction_truth_drift_without_freezing(tmp_path, monkeypatch):
    ctx, prepared, baseline = selection_fixture(tmp_path, adapted_wins=True)
    monkeypatch.setattr(execution, "verified_control", lambda ctx: baseline)
    path = ctx["run"] / "training/pass-0002-validation-predictions-private.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[0]["truth"] = "a"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    with pytest.raises(ValueError, match="truth_mismatch"):
        execution.freeze_selection(ctx, prepared)
    assert not (ctx["run"] / "winner-frozen.json").exists()
