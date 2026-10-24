"""Recovery protocol and telemetry use inert weights, token lists and fake runtime."""

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from research.english.r5_data.common import digest_file, write_json
from research.english.r5_eval.lifecycle import claim_final_test, file_inventory, freeze
from research.english.r5_eval.recovery import (
    RETRY_DIRECTORY,
    ProgressModel,
    prepare,
    run,
    verify_prepared,
)


@pytest.fixture
def interrupted(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    staging = tmp_path / "staging"
    (staging / "images").mkdir(parents=True)
    image_path = staging / "fixture.png"
    Image.new("L", (128, 128), 255).save(image_path)
    checksum = hashlib.sha256(image_path.read_bytes()).hexdigest()
    image_path.rename(staging / f"images/{checksum}.png")
    rows = [
        {
            "id": f"fixture-{index}",
            "label": letter,
            "writer": f"writer-{index}",
            "split": "test",
            "hash": checksum,
            "path": f"images/{checksum}.png",
        }
        for index, letter in enumerate(("A", "a"))
    ]
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "inert.txt").write_text("synthetic checkpoint only")
    lineage = {"data": "synthetic", "final_claim_ledger": str(tmp_path / "claims")}
    write_json(root / "session.json", lineage)
    evidence = root / "synthetic-validation.json"
    write_json(evidence, {"fixture": True})
    freeze(
        [
            {
                "name": "frozen-inert-control",
                "kind": "r4_full_checkpoint",
                "checkpoint_path": str(checkpoint),
                "checkpoint_files": file_inventory(checkpoint),
                "validation_metrics": {
                    "n": 2,
                    "correct": 1,
                    "accuracy": 0.5,
                    "scalar_cer": 0.5,
                    "exact_case_sensitive": True,
                },
                "per_crop_ms_p50": 1.0,
            }
        ],
        lineage=lineage,
        evidence=[evidence],
        output=root / "winner-frozen.json",
    )
    write_json(root / "execution-source.json", {"fixture": True})
    (root / "execution-source.zip").write_bytes(b"inert archive hash fixture")
    claim = claim_final_test(rows, root / "winner-frozen.json", Path(lineage["final_claim_ledger"]))
    output = root / "final-test"
    output.mkdir()
    write_json(output / "started.json", claim)
    write_json(
        output / "failure.json",
        {
            "status": "incomplete_claim_retained",
            "exception": "KeyboardInterrupt",
            "automatic_retry": False,
        },
    )
    return {
        "run": root,
        "lineage": lineage,
        "study": {"selected": {"test": rows, "school_test": []}},
        "staging": staging,
        "plan": SimpleNamespace(recipe=SimpleNamespace(max_validation_wall_seconds=60)),
    }


def install_inert_runtime(monkeypatch, *, interrupted=False):
    from research.english.r5_eval import execution

    loads = []

    class Tokens(list):
        def tolist(self):
            return list(self)

    class Model:
        def eval(self):
            pass

        def generate(self, **kwargs):
            assert kwargs["do_sample"] is False
            assert kwargs["num_beams"] == 1
            assert kwargs["max_new_tokens"] == 8
            if interrupted:
                raise KeyboardInterrupt
            return Tokens([[2, 4, 2] for _ in kwargs["pixel_values"]])

    class Budget:
        def phase(self, *args):
            return nullcontext()

        def report(self):
            return {"synthetic": True}

        def close(self):
            pass

    def runtime(ctx):
        loads.append(ctx)
        torch = SimpleNamespace(cat=lambda values, dim: values, inference_mode=nullcontext)

        def processor(**kwargs):
            return SimpleNamespace(pixel_values="inert")

        tokenizer = SimpleNamespace(eos_token_id=2, decode=lambda *args, **kwargs: "A")
        return torch, Model(), processor, tokenizer

    monkeypatch.setattr(execution, "_runtime", runtime)
    monkeypatch.setattr(execution, "_evaluation_budget", lambda *args: Budget())
    return execution, loads


def test_prepare_does_not_load_model_and_preserves_original_evidence(interrupted):
    ctx = interrupted
    paths = list(ctx["run"].rglob("*")) + list(Path(ctx["lineage"]["final_claim_ledger"]).iterdir())
    pins = {path: digest_file(path) for path in paths if path.is_file()}
    receipt = prepare(ctx)
    assert receipt["status"] == "prepared_without_model_execution"
    assert verify_prepared(ctx) == receipt
    assert prepare(ctx) == receipt
    assert all(digest_file(path) == expected for path, expected in pins.items())
    assert not (ctx["run"] / RETRY_DIRECTORY / "final-test").exists()


def test_owner_retry_runs_original_evaluator_once_and_keeps_original_claim(
    interrupted, monkeypatch, capsys
):
    ctx = interrupted
    receipt = prepare(ctx)
    original_claim = Path(receipt["original_claim_path"])
    original_hash = digest_file(original_claim)
    engine, loads = install_inert_runtime(monkeypatch)
    old_functions = (
        engine._runtime,
        engine.predict_rows,
        engine.score_predictions,
        engine.claim_final_test,
    )
    with pytest.raises(ValueError, match="explicit_owner_run"):
        run(ctx, owner_run=False, engine=engine)
    assert not loads
    result = run(ctx, owner_run=True, engine=engine)
    assert result["status"] == "measured_research_only"
    assert result["groups"]["primary"]["metrics"]["accuracy"] == 0.5
    assert result["claim"]["original_claim_sha256"] == original_hash
    assert result["claim"]["helper_sha256"] == receipt["helper_sha256"]
    assert digest_file(original_claim) == original_hash
    assert (ctx["run"] / "final-test/failure.json").is_file()
    assert not (ctx["run"] / "final-test/report.json").exists()
    assert (ctx["run"] / RETRY_DIRECTORY / "final-test/report.json").is_file()
    assert len(loads) == 1
    assert old_functions == (
        engine._runtime,
        engine.predict_rows,
        engine.score_predictions,
        engine.claim_final_test,
    )
    with pytest.raises(ValueError, match="already_claimed_or_started"):
        run(ctx, owner_run=True, engine=engine)
    assert len(loads) == 1
    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    progress = [line for line in messages if line["stage"] == "r5-final-evaluation"]
    assert [line["completed_samples"] for line in progress] == [0, 2]
    assert all("truth" not in line and "prediction" not in line for line in messages)


def test_interrupted_retry_keeps_second_claim_and_never_retries_implicitly(
    interrupted, monkeypatch
):
    ctx = interrupted
    prepare(ctx)
    engine, loads = install_inert_runtime(monkeypatch, interrupted=True)
    with pytest.raises(KeyboardInterrupt):
        run(ctx, owner_run=True, engine=engine)
    failure = json.loads((ctx["run"] / RETRY_DIRECTORY / "final-test/failure.json").read_text())
    assert failure["status"] == "incomplete_claim_retained"
    assert len(list(Path(ctx["lineage"]["final_claim_ledger"]).glob("*.json"))) == 2
    with pytest.raises(ValueError, match="already_claimed_or_started"):
        run(ctx, owner_run=True, engine=engine)
    assert len(loads) == 1


@pytest.mark.parametrize("damage", ["helper", "winner", "claim", "attempt", "predictions"])
def test_recovery_refuses_changed_evidence_before_runtime(interrupted, monkeypatch, damage):
    ctx = interrupted
    receipt = prepare(ctx)
    retry = ctx["run"] / RETRY_DIRECTORY
    if damage == "helper":
        (retry / "recovery-runner.py").write_text("tampered")
    elif damage == "winner":
        (retry / "winner-frozen.json").write_text("{}")
    elif damage == "claim":
        Path(receipt["original_claim_path"]).write_text("{}")
    elif damage == "attempt":
        (ctx["run"] / "final-test/failure.json").write_text("{}")
    else:
        (ctx["run"] / "final-test/primary-predictions-private.jsonl").write_text("{}")
    engine, loads = install_inert_runtime(monkeypatch)
    with pytest.raises(ValueError):
        run(ctx, owner_run=True, engine=engine)
    assert not loads


def test_progress_proxy_forwards_exact_arguments_and_results_without_text_leakage(capsys):
    calls = []

    class Model:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return kwargs["pixel_values"]

    first, second = [object()] * 64, [object()] * 16
    model = ProgressModel(Model(), 80)
    assert model.generate(pixel_values=first, do_sample=False, num_beams=1) is first
    assert model.generate(pixel_values=second, do_sample=False, num_beams=1) is second
    assert calls == [
        {"pixel_values": first, "do_sample": False, "num_beams": 1},
        {"pixel_values": second, "do_sample": False, "num_beams": 1},
    ]
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["completed_samples"] for item in output] == [0, 64, 80]
    assert all(
        set(item) == {"stage", "completed_samples", "total_samples", "elapsed_seconds"}
        for item in output
    )
