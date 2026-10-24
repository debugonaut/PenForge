"""Part A lifecycle and Part B adapter contracts; inert records/checkpoint bytes only."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from research.english.r5_contract import CLASSES, FIELDS
from research.english.r5_eval.lifecycle import (
    claim_final_test,
    file_inventory,
    freeze,
    verify_frozen,
)
from research.english.r5_eval.scoring import validation_scorer


def candidates(tmp_path):
    result = []
    for name, correct in (("control", 1), ("adapted", 2)):
        path = tmp_path / name
        path.mkdir()
        (path / "weights.txt").write_text("inert fixture")
        result.append(
            {
                "name": name,
                "checkpoint_path": str(path),
                "checkpoint_files": file_inventory(path),
                "validation_metrics": {
                    "n": 2,
                    "correct": correct,
                    "accuracy": correct / 2,
                    "scalar_cer": 1 - correct / 2,
                    "exact_case_sensitive": True,
                },
                "per_crop_ms_p50": 10.0,
            }
        )
    return result


def test_freeze_selects_validation_winner_and_detects_later_weight_or_report_changes(tmp_path):
    items = candidates(tmp_path)
    evidence = tmp_path / "training-report.json"
    evidence.write_text("{}")
    path = tmp_path / "winner.json"
    lineage = {"data": "frozen", "sources": "frozen"}
    value = freeze(items, lineage=lineage, evidence=[evidence], output=path)
    assert value["winner"]["name"] == "adapted"
    assert verify_frozen(path, lineage) == value
    with pytest.raises(FileExistsError):
        freeze(items, lineage=lineage, evidence=[evidence], output=path)
    evidence.write_text("changed")
    with pytest.raises(ValueError, match="evidence_changed"):
        verify_frozen(path, lineage)
    evidence.write_text("{}")
    (Path(items[1]["checkpoint_path"]) / "weights.txt").write_text("changed")
    with pytest.raises(ValueError, match="checkpoint_changed"):
        verify_frozen(path, lineage)


def test_final_claim_persists_independently_of_new_output_directory_and_record_order(tmp_path):
    winner = tmp_path / "winner.json"
    winner.write_text("{}")
    rows = [
        {"id": "one", "label": "A", "hash": "1" * 64, "writer": "fresh", "split": "test"},
        {"id": "two", "label": "a", "hash": "2" * 64, "writer": "other", "split": "test"},
    ]
    ledger = tmp_path / "ledger"
    value = claim_final_test(rows, winner, ledger)
    assert value["status"] == "claimed_before_model_load"
    assert len(list(ledger.glob("*.json"))) == 1
    with pytest.raises(ValueError, match="already_claimed"):
        claim_final_test(list(reversed(rows)), winner, ledger)
    with pytest.raises(ValueError, match="test_rows"):
        claim_final_test([{**rows[0], "split": "validation"}], winner, ledger)


def test_part_b_contract_and_inert_prediction_models_match_part_a():
    from research.english.r5_model.evaluate import call_scorer, records_for, validate_score
    from research.english.r5_model.rows import CLASSES as MODEL_CLASSES
    from research.english.r5_model.rows import SAMPLE_FIELDS

    assert tuple(MODEL_CLASSES) == CLASSES
    assert set(SAMPLE_FIELDS) == FIELDS
    rows = [
        {"id": "one", "label": "A", "writer": "first", "split": "validation"},
        {"id": "two", "label": "a", "writer": "second", "split": "validation"},
    ]
    records = records_for(list(reversed(rows)), ["A", "A"])
    result = call_scorer(validation_scorer(rows, rounds=100), records, rows)
    assert result["correct"] == 1
    assert result["by_writer"]["first"]["correct"] == 1
    assert result["by_writer"]["second"]["correct"] == 0
    assert validate_score(result, samples=2)["accuracy"] == 0.5


def test_part_b_training_adapter_rejects_final_rows_before_model_execution(tmp_path):
    from research.english.r5_model.rows import load_study

    (tmp_path / "study.jsonl").write_text(json.dumps({"split": "test"}) + "\n")
    with pytest.raises(ValueError):
        load_study(tmp_path)


@pytest.mark.parametrize(
    "stage",
    [
        "prepare",
        "preflight",
        "baseline",
        "train",
        "freeze",
        "final-test",
        "prepare-final-retry",
        "retry-final-test",
    ],
)
def test_shell_stage_dispatch_is_explicit_and_works_with_macos_bash_empty_array_rules(
    tmp_path, stage
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path("scripts/train_english_r5_research.sh")
    target = scripts / source.name
    shutil.copyfile(source, target)
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    python.chmod(0o700)
    result = subprocess.run(
        ["bash", str(target), stage], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert ("--owner-run" in result.stdout.splitlines()) == (
        stage in {"baseline", "train", "final-test", "retry-final-test"}
    )
    if stage in {"prepare-final-retry", "retry-final-test"}:
        assert "research.english.r5_eval.recovery" in result.stdout.splitlines()
        assert ("--prepare-only" in result.stdout.splitlines()) == (stage == "prepare-final-retry")
    assert "final-test" not in result.stdout.splitlines() if stage != "final-test" else True


def test_execution_requires_authorization_before_runtime_or_environment_changes():
    from research.english.r5_eval.execution import require_execution

    with pytest.raises(ValueError, match="explicit_owner_run"):
        require_execution(False)


def test_effective_checkpoint_rotation_limit_is_finite_and_covers_repaired_state_layout(
    tmp_path,
):
    from research.english.r5_eval.execution import EFFECTIVE_OUTPUT_BYTES, bounded_budget
    from research.english.r5_model.budgets import BudgetPlan

    plan = BudgetPlan(3600, 900, 10800, 14400, 6000, 3_000_000_000)
    budget = bounded_budget(plan, tmp_path)
    assert 2_352_451_584 < budget.plan.output_bytes == EFFECTIVE_OUTPUT_BYTES == 3_000_000_000
    assert plan.output_bytes == 3_000_000_000
    assert budget.plan.run_wall_seconds == 10800


def test_saved_composite_token_ids_are_adapted_explicitly_without_changing_original_bytes(tmp_path):
    from research.english.r5_eval.execution import prepare_initialization

    source, bundle, run = [tmp_path / name for name in ("source", "bundle", "run")]
    for folder in (source, bundle, run):
        folder.mkdir()
    ids = {"decoder_start_token_id": 2, "eos_token_id": 2, "pad_token_id": 1}
    (source / "config.json").write_text(json.dumps({**ids, "decoder": {"model_type": "trocr"}}))
    (bundle / "config.json").write_text(json.dumps({"decoder": ids}))
    (source / "model.safetensors").write_bytes(b"inert weight fixture")
    (source / "generation_config.json").write_text(json.dumps(ids))
    original = file_inventory(source)
    derived, receipt = prepare_initialization(source, bundle, run)
    value = json.loads((derived / "config.json").read_text())
    assert all(key not in value for key in ids)
    assert all(value["decoder"][key] == ids[key] for key in ids)
    assert receipt["weights_changed"] is False
    assert file_inventory(source) == original
    assert file_inventory(derived)["model.safetensors"] == original["model.safetensors"]
    assert prepare_initialization(source, bundle, run) == (derived, receipt)
    value["pad_token_id"] = 99
    (derived / "config.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="metadata_changed"):
        prepare_initialization(source, bundle, run)


def test_private_execution_snapshot_excludes_data_and_keeps_concurrent_origin_edits_separate(
    tmp_path,
):
    from research.english.r5_eval.snapshot import freeze_source

    origin, run = tmp_path / "origin", tmp_path / "run"
    files = {
        "pyproject.toml": "project",
        "uv.lock": "lock",
        "scripts/train_english_r5_research.sh": "shell",
        "research/ENGLISH_R5_MODEL.md": "complete",
        "research/english/r5_model/example.py": "before",
        "research/runs/private.json": "private",
        "research/data/private.json": "private",
        "research/english/image.png": "private",
        ".env": "private",
    }
    for name, value in files.items():
        path = origin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    target, manifest = freeze_source(run, source_root=origin)
    metadata = json.loads(manifest.read_text())
    assert len(metadata["files"]) == 5
    assert not (target / "research/runs/private.json").exists()
    assert not (target / "research/english/image.png").exists()
    assert not (target / ".env").exists()
    source = origin / "research/english/r5_model/example.py"
    source.write_text("concurrent change")
    assert freeze_source(run, source_root=origin) == (target, manifest)
    assert (target / source.relative_to(origin)).read_text() == "before"
    shutil.rmtree(target)
    assert freeze_source(run, source_root=origin) == (target, manifest)
    assert (target / source.relative_to(origin)).read_text() == "before"
    (target / "unexpected.py").write_text("unexpected")
    with pytest.raises(ValueError, match="inventory_changed"):
        freeze_source(run, source_root=origin)
    (target / "unexpected.py").unlink()
    (target / source.relative_to(origin)).write_text("changed")
    with pytest.raises(ValueError, match="frozen_execution_source_changed"):
        freeze_source(run, source_root=origin)
    shutil.rmtree(target)
