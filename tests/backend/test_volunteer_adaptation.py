"""Bounded volunteer adaptation and the W06–W09 external holdout.

No model runs here: the training lifecycle is exercised with a fake engine and injected
pass/validate callables, and the R6 staging rows are synthetic. The tests check the
mixture, the writer roles, the forgetting-guard selection rule, owner-only refusals and
the holdout's freeze-before-evaluation contract.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from research.english.volunteer import adaptation as adaptation_module
from research.english.volunteer import audit as audit_module
from research.english.volunteer import frozen as frozen_module
from research.english.volunteer import holdout as holdout_module
from research.english.volunteer.manifest import LOWER, UPPER, load_manifest
from tests.backend.volunteer_fixtures import build_pilot_run

ROOT = Path(__file__).resolve().parents[2]
CLASSES = UPPER + LOWER


def _python(code: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = f"{ROOT / 'packages/contracts'}:{ROOT}"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _r6_row(index: int, split: str) -> dict[str, Any]:
    label = CLASSES[index % 52]
    digest = hashlib.sha256(f"{split}-{index}".encode()).hexdigest()
    return {
        "id": f"{split}-{index}",
        "hash": digest,
        "path": f"images/{digest}.png",
        "pixel_hash": hashlib.sha256(f"pixel-{split}-{index}".encode()).hexdigest(),
        "label": label,
        "writer": f"r6writer{index % 1736}",
        "source_group": "letters",
        "source_partition": "cvl",
        "split": split,
        "source_member": f"member-{index}.png",
    }


class FakeStudy:
    """The admitted R6 mapping, synthesized: 6,656 train and 1,664 validation rows."""

    def __init__(self, staging: Path) -> None:
        self.staging = staging
        self.train = tuple(_r6_row(index, "train") for index in range(6656))
        self.validation = tuple(_r6_row(index, "validation") for index in range(1664))
        self.mapping_sha256 = "a" * 64

    def lineage(self) -> dict[str, Any]:
        def summary(rows: tuple[dict[str, Any], ...]) -> dict[str, Any]:
            labels = Counter(row["label"] for row in rows)
            return {
                "samples": len(rows),
                "writers": len({row["writer"] for row in rows}),
                "classes": len(labels),
                "per_class": dict(sorted(labels.items())),
                "min_per_class": min(labels.values()),
                "sample_ids_sha256": "b" * 64,
                "content_hashes_sha256": "c" * 64,
                "pixel_hashes_sha256": "d" * 64,
                "writers_sha256": "e" * 64,
            }

        return {"splits": {"train": summary(self.train), "validation": summary(self.validation)}}


@pytest.fixture()
def pilot(tmp_path: Path) -> dict[str, Any]:
    return build_pilot_run(tmp_path / "pilot")


@pytest.fixture()
def prepared_pilot(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """Patch the frozen winner, the pilot verification and the R6 mapping for tests."""
    from research.english.r6_model import rows as rows_module

    study = FakeStudy(tmp_path / "staging")

    def fake_frozen(run: Path) -> dict[str, Any]:
        return {
            "frozen_run": str(run),
            "winner_name": frozen_module.WINNER_NAME,
            "winner_document_sha256": frozen_module.WINNER_DOCUMENT_SHA256,
            "checkpoint_path": str(Path(run) / "training/best"),
            "checkpoint_sha256": frozen_module.WINNER_CHECKPOINT_SHA256,
            "pass_index": frozen_module.PASS_INDEX,
            "composition": "pinned_r5_canonical_base_plus_r5_delta_plus_selected_r6_delta",
            "bundle": {"path": "/private/bundle", "files": {}},
            "initialization": {
                "base_checkpoint": "/private/base",
                "base_files": {},
                "trainable_inventory": {"p": {"dtype": "float32", "shape": [2]}},
            },
            "inference": dict(frozen_module.INFERENCE),
        }

    manifest = load_manifest(pilot["run"] / "crop-manifest.json")
    monkeypatch.setattr(
        adaptation_module.frozen_module, "verify_frozen_winner", lambda run: fake_frozen(run)
    )
    monkeypatch.setattr(
        adaptation_module.comparison_module,
        "verify_pilot",
        lambda run: (
            Path(run).resolve(),
            manifest,
            {"samples": {"accuracy": 0.5076923076923077, "correct": 132}},
            dict(pilot["baseline"]),
        ),
    )
    monkeypatch.setattr(rows_module, "load_study", lambda staging, **kwargs: study)
    monkeypatch.setattr(
        adaptation_module,
        "_frozen_session",
        lambda path: {
            "version": "r6-session-v1",
            "model_lineage": {"rows": {"splits": study.lineage()["splits"]}},
            "model_lineage_hash": "f" * 64,
        },
    )
    # The crop audit is a required input of the adaptation dataset.
    audit_module.build(pilot["run"])
    return {"study": study, "manifest": manifest, "frozen": fake_frozen}


def test_prepare_records_the_exact_mixture_and_writer_ancestry(
    tmp_path: Path, pilot: dict[str, Any], prepared_pilot: dict[str, Any]
) -> None:
    run = tmp_path / "adaptation"
    report = adaptation_module.prepare(pilot["run"], tmp_path / "staging", run)
    clean = {
        sample_id
        for sample_id, record in audit_module.classify(dict(pilot["baseline"])).items()
        if not record["crop_defect"]
    }
    assert report["mixture_samples"] == 6656 + len(clean)
    assert report["volunteer_excluded"] + report["volunteer_samples"] == 260
    assert report["volunteer_samples"] == len(clean)
    assert report["volunteer_share"] < 0.1
    assert report["validation_samples"] == 1664
    assert report["model_execution"] is False

    rows = [
        json.loads(line) for line in (run / adaptation_module.MIXTURE_NAME).read_text().splitlines()
    ]
    volunteer = [row for row in rows if row["kind"] == "volunteer"]
    assert len(volunteer) == len(clean)
    assert {row["writer"] for row in volunteer} == {"W01", "W02", "W03", "W04", "W05"}
    assert all(row["role"] == adaptation_module.DEVELOPMENT_ROLE for row in volunteer)
    assert all(row["label"] in CLASSES for row in volunteer)
    # Page ancestry is preserved for every development crop.
    manifest = prepared_pilot["manifest"]
    sources = {sample.sample_id: sample.source_sha256 for sample in manifest.samples}
    assert all(row["source_sha256"] == sources[row["sample_id"]] for row in volunteer)
    second = adaptation_module.prepare(pilot["run"], tmp_path / "staging", run)
    assert second["files_written"] == 0


def test_prepare_refuses_an_excessive_volunteer_share(
    tmp_path: Path, pilot: dict[str, Any], prepared_pilot: dict[str, Any]
) -> None:
    config = dataclasses.replace(
        adaptation_module.load_config(), volunteer_max_share=0.0, sha256="0" * 64
    )
    with pytest.raises(ValueError, match="volunteer_share_exceeds"):
        adaptation_module.prepare(pilot["run"], tmp_path / "staging", tmp_path / "a", config=config)


def test_prepare_refuses_a_writer_overlap_with_the_r6_mapping(
    tmp_path: Path,
    pilot: dict[str, Any],
    prepared_pilot: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from research.english.r6_model import rows as rows_module

    study = prepared_pilot["study"]
    collision = dict(study.train[0])
    collision["writer"] = "W01"
    study.train = (collision, *study.train[1:])
    with pytest.raises(ValueError, match="writer_overlap"):
        adaptation_module.prepare(pilot["run"], tmp_path / "staging", tmp_path / "a")
    del rows_module


def test_verify_prepared_refuses_a_changed_r6_checkpoint(
    tmp_path: Path,
    pilot: dict[str, Any],
    prepared_pilot: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = tmp_path / "adaptation"
    adaptation_module.prepare(pilot["run"], tmp_path / "staging", run)
    monkeypatch.setattr(
        adaptation_module.frozen_module,
        "verify_frozen_winner",
        lambda path: {**prepared_pilot["frozen"](path), "checkpoint_sha256": "0" * 64},
    )
    with pytest.raises(ValueError, match="frozen_winner_changed_after_preparation"):
        adaptation_module.verify_prepared(
            run,
            pilot_run=pilot["run"],
            staging=tmp_path / "staging",
        )


def test_run_refuses_without_the_owner_acknowledgment(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="owner_run_acknowledgment"):
        adaptation_module.run(
            tmp_path / "run",
            owner_run=False,
            pilot_run=tmp_path / "pilot",
            staging=tmp_path / "staging",
        )


def test_adaptation_runner_requires_the_owner_flag() -> None:
    result = _python(
        "from pathlib import Path\n"
        "import sys\n"
        "from research.english import volunteer_run\n"
        "sys.argv = ['volunteer_run', 'adapt', '--run', '/tmp/x']\n"
        "try:\n    volunteer_run.main()\n"
        "except ValueError as error:\n    print(error)\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "volunteer_adapt_requires_owner_run"


def _fake_run_inputs(tmp_path: Path, pilot: dict[str, Any]) -> dict[str, Any]:
    study = FakeStudy(tmp_path / "staging")
    rows = [dict(row) for row in study.train[:52]]
    root = tmp_path / "adaptation-run"
    root.mkdir(exist_ok=True)
    prepared = {
        "candidate": adaptation_module.CANDIDATE,
        "pilot": {"run": str(pilot["run"])},
        "mixture": {
            "sha256": "a" * 64,
            "samples": len(rows),
            "volunteer_samples": 8,
            "volunteer_share": 8 / len(rows),
        },
        "validation": {"samples": 52, "forgetting_floor_correct": 40},
        "frozen_winner": {
            "checkpoint_sha256": frozen_module.WINNER_CHECKPOINT_SHA256,
            "winner_name": frozen_module.WINNER_NAME,
            "initialization": {"trainable_inventory": {"p": {"dtype": "float32", "shape": [2]}}},
        },
    }
    return {"root": root, "rows": rows, "prepared": prepared, "study": study}


def test_volunteer_training_pixels_use_actual_capture_transform_without_a_model(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (96, 64), (246, 244, 239))
    for y in range(18, 44):
        for x in range(36, 44):
            image.putpixel((x, y), (25, 30, 45))
    crop = tmp_path / "letter.png"
    image.save(crop)
    source_bytes = crop.read_bytes()
    config = adaptation_module.load_config()
    config = dataclasses.replace(
        config, capture=dataclasses.replace(config.capture, augmentation_probability=1.0)
    )
    from research.english.volunteer import capture

    plan = capture.plan_for(config.capture, sample_id="vol-W01-upper-A", epoch=9)
    assert plan.perspective_fraction != 0

    class Processor:
        size = {"height": 384, "width": 384}

        def __call__(self, *, images: Image.Image, return_tensors: str, do_resize: bool):
            assert images.mode == "RGB"
            assert images.size == (384, 384)
            assert return_tensors == "pt"
            assert do_resize is False
            return SimpleNamespace(pixel_values=images.tobytes())

    engine = adaptation_module._OwnerEngine()
    engine.processor = Processor()
    row = {
        "id": "vol-W01-upper-A",
        "kind": "volunteer",
        "path": crop.name,
        "crop_sha256": hashlib.sha256(source_bytes).hexdigest(),
    }
    first = engine.training_pixels(
        row, staging=tmp_path, pilot_root=tmp_path, config=config, epoch=9
    )
    second = engine.training_pixels(
        row, staging=tmp_path, pilot_root=tmp_path, config=config, epoch=9
    )
    assert first == second
    assert crop.read_bytes() == source_bytes
    assert engine.torch is None
    assert engine.model is None


def test_verify_refuses_a_changed_capture_implementation_version(
    tmp_path: Path, pilot: dict[str, Any], prepared_pilot: dict[str, Any]
) -> None:
    root = tmp_path / "adaptation"
    adaptation_module.prepare(pilot["run"], tmp_path / "staging", root)
    path = root / adaptation_module.PREPARED_NAME
    prepared = json.loads(path.read_text())
    prepared["config"]["capture"]["version"] = "penforge-volunteer-capture-simulation-v1"
    path.write_text(json.dumps(prepared))
    with pytest.raises(ValueError, match="config_changed_after_preparation"):
        adaptation_module.verify_prepared(
            root, pilot_run=pilot["run"], staging=tmp_path / "staging"
        )


def test_partial_attempt_cannot_restart_and_truncate_loss_log(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _fake_run_inputs(tmp_path, pilot)
    monkeypatch.setattr(
        adaptation_module,
        "verify_prepared",
        lambda *args, **kwargs: (
            inputs["root"],
            inputs["prepared"],
            inputs["rows"],
            inputs["study"],
        ),
    )
    log = inputs["root"] / "pass-0001-loss-private.jsonl"
    evidence = '{"step": 11}\n'
    log.write_text(evidence)
    engine = FakeEngine()
    with pytest.raises(FileExistsError, match="partial_run_requires_resume_or_new_directory"):
        adaptation_module.run(
            inputs["root"],
            owner_run=True,
            pilot_run=pilot["run"],
            staging=tmp_path / "staging",
            engine=engine,
        )
    with pytest.raises(adaptation_module.AdaptationError, match="resume_requires_a_complete_pass"):
        adaptation_module.run(
            inputs["root"],
            owner_run=True,
            pilot_run=pilot["run"],
            staging=tmp_path / "staging",
            engine=engine,
            resume=True,
        )
    assert engine.setup_calls == 0
    assert log.read_text() == evidence


class FakeEngine:
    def __init__(self) -> None:
        self.setup_calls = 0
        self.loaded: list[int] = []

    def setup(self, prepared: dict[str, Any], config: Any) -> None:
        self.setup_calls += 1

    def save(self, directory: Path, *, pass_index: int, data_epoch: int, step: int):
        directory.mkdir(parents=True)
        (directory / "state.json").write_text(
            json.dumps({"pass_index": pass_index, "data_epoch": data_epoch, "step": step})
        )
        return {"tensors_sha256": f"{pass_index:064d}"}

    def load(self, directory: Path, *, expected_pass: int) -> dict[str, Any]:
        self.loaded.append(expected_pass)
        return {"pass_index": expected_pass}

    def publish_selected(
        self, source: Path, destination: Path, *, expected_pass: int, expected_sha256: str
    ) -> dict[str, Any]:
        assert source.name == f"pass-{expected_pass:04d}"
        assert expected_sha256 == f"{expected_pass:064d}"
        destination.mkdir(parents=True)
        state = {
            "pass_index": expected_pass,
            "weights_only": True,
            "source_state_sha256": expected_sha256,
            "tensors_sha256": "e" * 64,
        }
        (destination / "state.json").write_text(json.dumps(state))
        return state


def test_output_preflight_rejects_native_ceiling_and_accepts_resource_only_recipe(
    tmp_path: Path,
) -> None:
    native = adaptation_module.load_config()
    derived = adaptation_module.load_config(
        ROOT / "research/configs/english-volunteer-adaptation-3gb.json"
    )
    assert dict(derived.raw) == {**native.raw, "max_output_bytes": 3_000_000_000}
    prepared = {
        "frozen_winner": {
            "initialization": {
                "trainable_inventory": {"p": {"dtype": "float32", "shape": [84_016_128]}}
            }
        }
    }
    with pytest.raises(adaptation_module.AdaptationError, match="checkpoint_plan_exceeds"):
        adaptation_module.output_plan(tmp_path, prepared, native)
    plan = adaptation_module.output_plan(tmp_path, prepared, derived)
    assert 2_350_000_000 < int(plan["estimated_peak_bytes"]) < 2_400_000_000
    # A full-state best/ would exceed even the earlier 3 GB ceiling before metadata.
    assert 3 * 3 * 336_064_512 > 3_000_000_000


def test_selected_publication_saves_only_model_tensors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved_keys: list[set[str]] = []

    def save_file(tensors: dict[str, Any], path: str) -> None:
        saved_keys.append(set(tensors))
        Path(path).write_text(json.dumps(sorted(tensors)))

    monkeypatch.setitem(sys.modules, "safetensors.torch", SimpleNamespace(save_file=save_file))

    class Tensor:
        def detach(self) -> Tensor:
            return self

        def to(self, device: str) -> Tensor:
            return self

        def contiguous(self) -> Tensor:
            return self

        def clone(self) -> Tensor:
            return self

    parameter = Tensor()
    engine = adaptation_module._OwnerEngine()
    engine.named = [("p", parameter)]
    engine.inventory = {"p": {"dtype": "float32", "shape": [2]}}
    engine.optimizer = SimpleNamespace(
        state={parameter: {"exp_avg": Tensor(), "exp_avg_sq": Tensor(), "step": Tensor()}}
    )
    engine.torch = SimpleNamespace(get_rng_state=lambda: SimpleNamespace(tolist=lambda: [0]))
    full = engine.save(tmp_path / "full", pass_index=1, data_epoch=9, step=1715)
    assert saved_keys[0] == {
        "model.p",
        "optimizer.p.exp_avg",
        "optimizer.p.exp_avg_sq",
        "optimizer.p.step",
    }
    loaded: list[int] = []

    def load(source: Path, *, expected_pass: int) -> dict[str, Any]:
        assert source == tmp_path / "full"
        loaded.append(expected_pass)
        return full

    monkeypatch.setattr(engine, "load", load)
    selected = engine.publish_selected(
        tmp_path / "full",
        tmp_path / "best",
        expected_pass=1,
        expected_sha256=full["tensors_sha256"],
    )
    assert loaded == [1]
    assert saved_keys[1] == {"model.p"}
    assert selected["weights_only"] is True
    assert selected["source_state_sha256"] == full["tensors_sha256"]
    assert selected["tensors_sha256"] != full["tensors_sha256"]


def test_incompatible_output_budget_refuses_before_engine_setup(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _fake_run_inputs(tmp_path, pilot)
    inputs["prepared"]["frozen_winner"]["initialization"]["trainable_inventory"] = {
        "p": {"dtype": "float32", "shape": [84_016_128]}
    }
    monkeypatch.setattr(
        adaptation_module,
        "verify_prepared",
        lambda *args, **kwargs: (
            inputs["root"],
            inputs["prepared"],
            inputs["rows"],
            inputs["study"],
        ),
    )
    engine = FakeEngine()
    with pytest.raises(adaptation_module.AdaptationError, match="checkpoint_plan_exceeds"):
        adaptation_module.run(
            inputs["root"],
            owner_run=True,
            pilot_run=pilot["run"],
            staging=tmp_path / "staging",
            engine=engine,
        )
    assert engine.setup_calls == 0
    assert not (inputs["root"] / "run-in-progress.json").exists()


@pytest.mark.parametrize(
    ("samples", "batch_size", "accumulation", "batches", "steps", "total"),
    [(6858, 1, 4, 6858, 1715, 3430), (5, 2, 2, 3, 2, 4), (8, 2, 2, 4, 2, 4)],
)
def test_step_plan_counts_partial_batches_and_accumulations(
    samples: int, batch_size: int, accumulation: int, batches: int, steps: int, total: int
) -> None:
    config = dataclasses.replace(
        adaptation_module.load_config(), batch_size=batch_size, accumulate_batches=accumulation
    )
    plan = adaptation_module.step_plan(samples, config)
    assert plan.batches_per_pass == batches
    assert plan.optimizer_steps_per_pass == steps
    assert plan.total_optimizer_steps == total


def test_real_pass_loop_keeps_per_pass_steps_separate_from_scheduler_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the actual loop with arithmetic fakes; no framework or model is loaded."""
    from research.english.r5_model import batching, objective, scheduler

    config = adaptation_module.load_config()
    rows = [
        {**_r6_row(index, "train"), "kind": "r6" if index < 6656 else "volunteer"}
        for index in range(6858)
    ]
    plan = adaptation_module.step_plan(len(rows), config)
    scheduled: list[tuple[int, int]] = []
    visited: list[str] = []
    optimizer_calls: list[int] = []
    multipliers: list[float] = []
    actual_multiplier = scheduler.learning_rate_multiplier

    def schedule(step: int, **kwargs: Any) -> float:
        scheduled.append((step, kwargs["total_steps"]))
        multiplier = actual_multiplier(step, **kwargs)
        multipliers.append(multiplier)
        return multiplier

    class ArithmeticLoss:
        def detach(self) -> float:
            return 1.0

        def __truediv__(self, count: int) -> ArithmeticLoss:
            return self

        def backward(self) -> None:
            pass

    def pixels(row: dict[str, Any], **kwargs: Any) -> str:
        visited.append(row["id"])
        return row["id"]

    monkeypatch.setattr(batching, "padded_label_ids", lambda *args: None)
    monkeypatch.setattr(objective, "teacher_forced_loss", lambda *args: ArithmeticLoss())
    monkeypatch.setattr(scheduler, "learning_rate_multiplier", schedule)
    engine = SimpleNamespace(
        torch=SimpleNamespace(
            cat=lambda values, dim: values,
            nn=SimpleNamespace(utils=SimpleNamespace(clip_grad_norm_=lambda *args, **kw: None)),
        ),
        model=object(),
        tokenizer=object(),
        named=[],
        training_pixels=pixels,
        optimizer=SimpleNamespace(
            zero_grad=lambda **kwargs: None,
            param_groups=[{"lr": config.learning_rate}],
            step=lambda: optimizer_calls.append(1),
        ),
    )
    budget = adaptation_module._Budget(
        pass_seconds=4200,
        validation_seconds=1200,
        run_seconds=10800,
        rss_mb=6000,
        output_bytes=1500000000,
        root=tmp_path,
    )
    monkeypatch.setattr(budget, "check", lambda **kwargs: None)
    for pass_index in (1, 2):
        result = adaptation_module._train_one_pass(
            engine,
            rows,
            staging=tmp_path,
            pilot_root=tmp_path,
            config=config,
            epoch=config.data_epoch_offset + pass_index,
            pass_index=pass_index,
            base_step=(pass_index - 1) * plan.optimizer_steps_per_pass,
            expected_steps_per_pass=plan.optimizer_steps_per_pass,
            planned_total_steps=plan.total_optimizer_steps,
            budget=budget,
            progress=None,
        )
        assert result["samples"] == 6858
        assert result["optimizer_steps"] == 1715
        assert result["volunteer_samples"] == 202
        logs = [
            json.loads(line)
            for line in (tmp_path / f"pass-{pass_index:04d}-loss-private.jsonl")
            .read_text()
            .splitlines()
        ]
        assert [entry["step"] for entry in logs] == list(
            range((pass_index - 1) * 1715 + 1, pass_index * 1715 + 1)
        )
    assert scheduled == [(step, 3430) for step in range(1, 3431)]
    assert len(optimizer_calls) == 3430
    assert Counter(visited) == {row["id"]: 2 for row in rows}
    assert multipliers[-1] == pytest.approx(config.final_learning_rate_ratio)


def test_changed_batch_grouping_refuses_before_engine_setup(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from research.english.r5_model import batching

    inputs = _fake_run_inputs(tmp_path, pilot)
    monkeypatch.setattr(
        adaptation_module,
        "verify_prepared",
        lambda *args, **kwargs: (
            inputs["root"],
            inputs["prepared"],
            inputs["rows"],
            inputs["study"],
        ),
    )
    actual_batches = batching.epoch_batches
    monkeypatch.setattr(batching, "epoch_batches", lambda *args: actual_batches(*args)[:-4])
    engine = FakeEngine()
    with pytest.raises(adaptation_module.AdaptationError, match="step_plan_changed"):
        adaptation_module.run(
            inputs["root"],
            owner_run=True,
            pilot_run=pilot["run"],
            staging=tmp_path / "staging",
            engine=engine,
        )
    assert engine.setup_calls == 0
    assert not (inputs["root"] / "run-in-progress.json").exists()


def test_pre_step_refusal_marks_the_run_stopped(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _fake_run_inputs(tmp_path, pilot)
    monkeypatch.setattr(
        adaptation_module,
        "verify_prepared",
        lambda *args, **kwargs: (
            inputs["root"],
            inputs["prepared"],
            inputs["rows"],
            inputs["study"],
        ),
    )

    def refuse(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise adaptation_module.AdaptationError("synthetic_pre_step_refusal")

    with pytest.raises(adaptation_module.AdaptationError, match="synthetic_pre_step_refusal"):
        adaptation_module.run(
            inputs["root"],
            owner_run=True,
            pilot_run=pilot["run"],
            staging=tmp_path / "staging",
            engine=FakeEngine(),
            train_pass=refuse,
        )
    marker = json.loads((inputs["root"] / "run-in-progress.json").read_text())
    assert marker["status"] == "stopped"
    assert marker["reason"] == "synthetic_pre_step_refusal"
    assert marker["automatic_retry"] is False
    assert not list(inputs["root"].glob("pass-*-loss-private.jsonl"))
    assert not (inputs["root"] / adaptation_module.CHECKPOINT_DIR).exists()


def _validation_records(rows: list[dict[str, Any]], correct: int) -> list[dict[str, Any]]:
    records = []
    for index, row in enumerate(rows):
        prediction = row["label"] if index < correct else row["label"].swapcase()
        records.append(
            {
                "sample_id": row["id"],
                "writer_id": row["writer"],
                "group": "upper" if row["label"].isupper() else "lower",
                "truth": row["label"],
                "prediction": prediction,
            }
        )
    return records


def _run_with_fakes(
    tmp_path: Path,
    pilot: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    per_pass_correct: tuple[int, ...],
    floor: int = 40,
    resume: bool = False,
    refuse_before_pass: int | None = None,
) -> dict[str, Any]:
    inputs = _fake_run_inputs(tmp_path, pilot)
    root, rows, prepared = inputs["root"], inputs["rows"], inputs["prepared"]
    study = inputs["study"]
    monkeypatch.setattr(
        adaptation_module,
        "verify_prepared",
        lambda *args, **kwargs: (root, prepared, rows, study),
    )
    engine = FakeEngine()
    config = dataclasses.replace(adaptation_module.load_config(), forgetting_floor_correct=floor)
    seen: list[int] = []

    def fake_train_pass(engine_arg: Any, mixture: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs["pass_index"] == refuse_before_pass:
            raise adaptation_module.AdaptationError("synthetic_interruption_between_passes")
        seen.append(int(kwargs["pass_index"]))
        assert kwargs["expected_steps_per_pass"] == 13
        assert kwargs["planned_total_steps"] == 26
        assert kwargs["base_step"] == (int(kwargs["pass_index"]) - 1) * 13
        return {
            "pass_index": int(kwargs["pass_index"]),
            "data_epoch": int(kwargs["epoch"]),
            "status": "complete_one_pass",
            "samples": len(mixture),
            "volunteer_samples": 8,
            "r6_samples": len(mixture) - 8,
            "optimizer_steps": 13,
            "mean_loss": 0.5,
            "wall_seconds": 0.01,
        }

    def fake_validate(engine_arg: Any, validation_rows: Any, **kwargs: Any):
        index = len(seen) - 1
        records = _validation_records(
            list(validation_rows), per_pass_correct[min(index, len(per_pass_correct) - 1)]
        )
        return records, {
            "samples": len(records),
            "wall_seconds": 0.01,
            "generation_limit_hits": 0,
            "per_crop_ms_p50": 0.5,
            "per_crop_ms_p95": 0.9,
        }

    return adaptation_module.run(
        root,
        owner_run=True,
        pilot_run=pilot["run"],
        staging=tmp_path / "staging",
        config=config,
        engine=engine,
        train_pass=fake_train_pass,
        validate_pass=fake_validate,
        resume=resume,
    )


def test_fake_runtime_lifecycle_selects_by_the_forgetting_guard(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _run_with_fakes(tmp_path, pilot, monkeypatch, per_pass_correct=(48, 44))
    assert report["status"] == "training_complete_candidate_selected"
    assert report["product_qualification"] == "none"
    assert report["capability_status"] == "experimental_volunteer_domain_adaptation"
    assert report["test_evaluated"] is False
    selection = report["selection"]
    assert selection["rule"] == adaptation_module.SELECTION_RULE
    assert selection["selected_pass"] == 1  # highest validation exact, then lowest pass index
    assert selection["forgetting_floor_correct"] == 40
    assert report["candidate_checkpoint"]["weights_only"] is True
    assert report["candidate_checkpoint"]["checkpoint_sha256"] == "e" * 64
    assert report["candidate_checkpoint"]["source_state_sha256"] == f"{1:064d}"
    root = _run_root_for(tmp_path)
    assert (root / "training/best/state.json").is_file()
    assert (root / "selection.json").is_file()
    assert (root / "pass-results.json").is_file()
    assert (root / "pass-0001-validation-predictions-private.jsonl").is_file()
    assert len(report["passes"]) == 2
    assert report["holdout"]["status"] == "unavailable_w06_w09_not_supplied"


def _run_root_for(tmp_path: Path) -> Path:
    return tmp_path / "adaptation-run"


def test_lifecycle_reports_a_failed_forgetting_guard(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _run_with_fakes(tmp_path, pilot, monkeypatch, per_pass_correct=(30, 20), floor=40)
    assert report["status"] == "training_complete_no_candidate_passed_the_forgetting_guard"
    assert report["selection"]["selected_pass"] is None
    assert not (_run_root_for(tmp_path) / "training/best").exists()


def test_resume_uses_the_completed_pass_and_keeps_the_global_schedule(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(adaptation_module.AdaptationError, match="interruption_between_passes"):
        _run_with_fakes(
            tmp_path, pilot, monkeypatch, per_pass_correct=(48, 44), refuse_before_pass=2
        )
    root = _run_root_for(tmp_path)
    assert (root / "checkpoints/pass-0001/state.json").exists()
    assert not (root / "checkpoints/pass-0002").exists()
    assert json.loads((root / "run-in-progress.json").read_text())["status"] == "stopped"
    report = _run_with_fakes(tmp_path, pilot, monkeypatch, per_pass_correct=(44,), resume=True)
    assert [item["pass_index"] for item in report["passes"]] == [1, 2]
    assert report["selection"]["selected_pass"] == 1
    saved = json.loads((root / "checkpoints/pass-0002/state.json").read_text())
    assert saved["step"] == 26
    assert not (root / "run-in-progress.json").exists()


def test_lifecycle_refuses_resume_without_a_complete_pass(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _fake_run_inputs(tmp_path, pilot)
    monkeypatch.setattr(
        adaptation_module,
        "verify_prepared",
        lambda *args, **kwargs: (
            inputs["root"],
            inputs["prepared"],
            inputs["rows"],
            inputs["study"],
        ),
    )
    with pytest.raises(adaptation_module.AdaptationError, match="resume_requires_a_complete_pass"):
        adaptation_module.run(
            inputs["root"],
            owner_run=True,
            pilot_run=pilot["run"],
            staging=tmp_path / "staging",
            engine=FakeEngine(),
            resume=True,
        )


def test_lifecycle_refuses_to_overwrite_a_completed_run(
    tmp_path: Path, pilot: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_with_fakes(tmp_path, pilot, monkeypatch, per_pass_correct=(48, 44))
    with pytest.raises(FileExistsError, match="already_completed"):
        _run_with_fakes(tmp_path, pilot, monkeypatch, per_pass_correct=(48, 44))


def test_selection_rule_prefers_the_guard_then_validation_exact() -> None:
    passes = [
        {"pass_index": 1, "samples": {"correct": 1360, "accuracy": 0.8}},
        {"pass_index": 2, "samples": {"correct": 1300, "accuracy": 0.78}},
    ]
    selection = adaptation_module.select_pass(passes, floor_correct=1348)
    assert selection["selected_pass"] == 1
    tied = [
        {"pass_index": 1, "samples": {"correct": 1360, "accuracy": 0.8}},
        {"pass_index": 2, "samples": {"correct": 1360, "accuracy": 0.8}},
    ]
    assert adaptation_module.select_pass(tied, floor_correct=1348)["selected_pass"] == 1
    failed = [
        {"pass_index": 1, "samples": {"correct": 1300, "accuracy": 0.78}},
        {"pass_index": 2, "samples": {"correct": 1310, "accuracy": 0.79}},
    ]
    selection = adaptation_module.select_pass(failed, floor_correct=1348)
    assert selection["selected_pass"] is None
    assert selection["status"] == "no_candidate_passed_the_forgetting_guard"


def test_validation_scoring_is_case_sensitive_and_records_folding() -> None:
    records = [
        {"sample_id": "a", "writer_id": "W01", "group": "upper", "truth": "A", "prediction": "A"},
        {"sample_id": "b", "writer_id": "W01", "group": "upper", "truth": "B", "prediction": "b"},
        {"sample_id": "c", "writer_id": "W02", "group": "lower", "truth": "c", "prediction": "d"},
    ]
    score = adaptation_module.score_records(records)
    assert score["correct"] == 1
    assert score["accuracy"] == pytest.approx(1 / 3)
    assert score["case_only_errors"] == 1
    assert score["casefolded_shape_accuracy"] == pytest.approx(2 / 3)
    assert score["by_writer"]["W01"]["correct"] == 1
    assert score["by_case"]["uppercase"]["correct"] == 1


def test_adaptation_stages_import_no_model_framework() -> None:
    result = _python(
        "import sys\n"
        "import research.english.volunteer.adaptation\n"
        "import research.english.volunteer.holdout\n"
        "import research.english.volunteer_run\n"
        "print(','.join(sorted(n for n in ('torch', 'transformers', 'safetensors')"
        " if n in sys.modules)))"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


# ----------------------------------------------------------------- W06-W09 holdout


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


@pytest.fixture()
def holdout_coordinates(tmp_path: Path) -> Path:
    writers = []
    for index, writer in enumerate(holdout_module.HOLDOUT_WRITERS):
        page = Image.new("RGB", (200, 300), (240, 238, 232))
        for offset in range(index + 1):  # every page must be a distinct byte sequence
            for x in range(120, 190):
                page.putpixel((x, 250 + offset), (40 + 8 * index, 40, 60))
        payload = _png(page)
        path = tmp_path / f"{writer}.png"
        path.write_bytes(payload)
        boxes = [
            {
                "label": label,
                "group": "upper",
                "x0": 10 + i * 6,
                "y0": 20,
                "x1": 24 + i * 6,
                "y1": 60,
            }
            for i, label in enumerate(UPPER)
        ]
        boxes += [
            {
                "label": label,
                "group": "lower",
                "x0": 10 + i * 6,
                "y0": 120,
                "x1": 24 + i * 6,
                "y1": 160,
            }
            for i, label in enumerate(LOWER)
        ]
        writers.append(
            {
                "writer_id": writer,
                "source_path": str(path),
                "source_sha256": hashlib.sha256(payload).hexdigest(),
                "source_bytes": len(payload),
                "page_width": 200,
                "page_height": 300,
                "boxes": boxes,
            }
        )
    registry = {
        "version": holdout_module.REGISTRY_VERSION,
        "role": holdout_module.HOLDOUT_ROLE,
        "frozen_before_model_evaluation": True,
        "excluded_from_training": True,
        "excluded_from_preprocessing_selection": True,
        "writers": writers,
    }
    path = tmp_path / "holdout-coordinates.json"
    path.write_text(json.dumps(registry, indent=2, sort_keys=True))
    return path


def test_holdout_refuses_before_the_four_pages_arrive(tmp_path: Path) -> None:
    with pytest.raises(holdout_module.HoldoutUnavailable, match="w06_w09"):
        holdout_module.prepare(tmp_path / "missing.json", tmp_path / "run")
    with pytest.raises(holdout_module.HoldoutUnavailable, match="w06_w09"):
        holdout_module.load_prepared(tmp_path / "run")


def test_holdout_registry_rejects_development_writers_and_missing_flags(
    holdout_coordinates: Path,
) -> None:
    registry = json.loads(holdout_coordinates.read_text())
    registry["writers"][0]["writer_id"] = "W01"
    with pytest.raises(ValueError, match="writer_order_changed"):
        holdout_module.validate_registry(registry)

    registry = json.loads(holdout_coordinates.read_text())
    del registry["excluded_from_training"]
    with pytest.raises(ValueError, match="excluded_from_training"):
        holdout_module.validate_registry(registry)

    registry = json.loads(holdout_coordinates.read_text())
    registry["writers"][1]["boxes"] = registry["writers"][1]["boxes"][:-1]
    with pytest.raises(ValueError, match="52_boxes"):
        holdout_module.validate_registry(registry)


def test_holdout_prepare_cuts_the_same_contract_and_freezes_roles(
    tmp_path: Path, holdout_coordinates: Path
) -> None:
    run = tmp_path / "holdout"
    report = holdout_module.prepare(holdout_coordinates, run)
    assert report["included_samples"] == holdout_module.EXPECTED_SAMPLES
    assert report["writers"] == list(holdout_module.HOLDOUT_WRITERS)
    assert report["model_execution"] is False
    second = holdout_module.prepare(holdout_coordinates, run)
    assert second["files_written"] == 0

    root, prepared, manifest = holdout_module.load_prepared(run)
    assert manifest["role"] == holdout_module.HOLDOUT_ROLE
    assert len(manifest["samples"]) == 208
    assert prepared["status"] == "prepared_awaiting_owner_evaluation"
    roles = json.loads((run / holdout_module.ROLES_NAME).read_text())
    assert roles["frozen_before_model_evaluation"] is True
    assert roles["excluded_from_training"] is True
    assert roles["excluded_from_preprocessing_selection"] is True

    sample = manifest["samples"][0]
    crop = root / sample["crop_path"]
    crop.write_bytes(crop.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="crop_changed"):
        holdout_module.load_prepared(run)


def test_holdout_compare_reports_paired_fixes_and_regressions(
    tmp_path: Path, holdout_coordinates: Path
) -> None:
    run = tmp_path / "holdout"
    holdout_module.prepare(holdout_coordinates, run)
    _, _, manifest = holdout_module.load_prepared(run)
    samples = manifest["samples"]

    def write_predictions(path: Path, mapping: dict[str, str]) -> None:
        path.write_text(
            "".join(
                json.dumps(
                    {
                        "version": holdout_module.PREDICTION_VERSION,
                        "sample_id": sample["sample_id"],
                        "writer_id": sample["writer_id"],
                        "truth": sample["truth"],
                        "raw_prediction": mapping[sample["sample_id"]],
                    },
                    sort_keys=True,
                )
                + "\n"
                for sample in samples
            )
        )

    baseline = {
        sample["sample_id"]: (sample["truth"].swapcase() if index % 3 == 0 else sample["truth"])
        for index, sample in enumerate(samples)
    }
    adapted = dict(baseline)
    for index, sample in enumerate(samples):
        if index % 3 == 0 and index < 12:
            adapted[sample["sample_id"]] = sample["truth"]
        elif index % 3 == 1 and index < 24:
            adapted[sample["sample_id"]] = sample["truth"].swapcase()
    write_predictions(tmp_path / "baseline.jsonl", baseline)
    write_predictions(tmp_path / "adapted.jsonl", adapted)

    comparison = holdout_module.compare(
        run,
        baseline_predictions=tmp_path / "baseline.jsonl",
        adapted_predictions=tmp_path / "adapted.jsonl",
    )
    assert comparison["label"] == holdout_module.LABEL
    assert comparison["product_qualification"] == "none"
    assert comparison["model_execution"] is False
    assert comparison["writers"] == list(holdout_module.HOLDOUT_WRITERS)
    paired = comparison["paired"]
    assert paired["fix_count"] > 0
    assert paired["regression_count"] > 0
    assert comparison["baseline"]["samples"]["by_writer"]["W06"]["samples"] == 52
    again = holdout_module.compare(
        run,
        baseline_predictions=tmp_path / "baseline.jsonl",
        adapted_predictions=tmp_path / "adapted.jsonl",
    )
    assert again["paired"] == paired

    write_predictions(tmp_path / "changed.jsonl", adapted)
    adapted_overwrite = tmp_path / "changed.jsonl"
    flipped = json.loads(adapted_overwrite.read_text().splitlines()[0])
    flipped["raw_prediction"] = "ZZ"
    lines = adapted_overwrite.read_text().splitlines()
    lines[0] = json.dumps(flipped, sort_keys=True)
    adapted_overwrite.write_text("\n".join(lines) + "\n")
    with pytest.raises(FileExistsError, match="already_exists"):
        holdout_module.compare(
            run,
            baseline_predictions=tmp_path / "baseline.jsonl",
            adapted_predictions=adapted_overwrite,
        )


def test_holdout_predictions_are_joined_by_exact_sample_id() -> None:
    with pytest.raises(ValueError, match="inventory_changed"):
        holdout_module.paired(
            {"W06-upper-A": {"sample_id": "W06-upper-A", "truth": "A", "prediction": "a"}},
            {},
        )
