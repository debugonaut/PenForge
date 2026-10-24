"""Partial alphabets and paired owner evaluation, using pixels and inert model doubles."""

from __future__ import annotations

import builtins
import hashlib
import json
import struct
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw

from research.english.volunteer import frozen, holdout, holdout_evaluate


@contextmanager
def model_frameworks_blocked() -> Iterator[None]:
    """Reject import requests even if unrelated suite tests loaded a framework earlier."""
    original = builtins.__import__

    def guarded(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".")[0] in {"torch", "transformers", "safetensors"}:
            raise AssertionError(f"unexpected framework import: {name}")
        return original(name, *args, **kwargs)

    with patch("builtins.__import__", guarded):
        yield


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


@pytest.fixture()
def registry(tmp_path: Path) -> Path:
    writers = []
    for index, writer_id in enumerate(holdout.HOLDOUT_WRITERS):
        page = Image.new("RGB", (100, 100), (245 - index, 240, 235))
        draw = ImageDraw.Draw(page)
        draw.line((12, 35, 20, 12, 28, 35), fill="black", width=2)
        draw.ellipse((50, 15, 65, 34), outline="black", width=2)
        source = tmp_path / f"{writer_id}.png"
        page.save(source)
        writers.append(
            {
                "writer_id": writer_id,
                "source_path": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_bytes": source.stat().st_size,
                "page_width": 100,
                "page_height": 100,
                "boxes": [
                    {"label": "A", "group": "upper", "x0": 10, "y0": 10, "x1": 30, "y1": 38},
                    {"label": "a", "group": "lower", "x0": 48, "y0": 12, "x1": 68, "y1": 38},
                ],
                "excluded": [
                    {"label": letter, "reason": "absent_on_page"}
                    for letter in holdout.UPPER + holdout.LOWER
                    if letter not in "Aa"
                ],
            }
        )
    path = tmp_path / "registry.json"
    write_json(
        path,
        {
            "version": holdout.PARTIAL_REGISTRY_VERSION,
            "role": holdout.HOLDOUT_ROLE,
            "frozen_before_model_evaluation": True,
            "excluded_from_training": True,
            "excluded_from_preprocessing_selection": True,
            "writers": writers,
        },
    )
    return path


def test_partial_inventory_preserves_missing_cases_and_legacy_refusal(registry: Path) -> None:
    payload = json.loads(registry.read_text())
    writers = holdout.validate_registry(payload)
    assert [len(writer.boxes) for writer in writers] == [2] * 4
    assert all(len(writer.excluded) == 50 for writer in writers)
    payload["version"] = holdout.REGISTRY_VERSION
    with pytest.raises(ValueError, match="52_boxes"):
        holdout.validate_registry(payload)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing_exclusion", "account_for_missing"),
        ("duplicate", "duplicate_or_empty"),
        ("overlap", "overlapping_boxes"),
        ("case", "case_mismatch"),
        ("order", "label_order"),
        ("empty", "52_boxes"),
    ],
)
def test_partial_registry_rejects_invalid_observations(
    registry: Path, change: str, reason: str
) -> None:
    payload = json.loads(registry.read_text())
    writer = payload["writers"][0]
    if change == "missing_exclusion":
        writer["excluded"].pop()
    elif change == "duplicate":
        writer["boxes"].append(dict(writer["boxes"][0]))
    elif change == "overlap":
        writer["boxes"][1].update(x0=15, x1=35)
    elif change == "case":
        writer["boxes"][0]["group"] = "lower"
    elif change == "order":
        writer["boxes"].reverse()
    else:
        writer["boxes"] = []
    with pytest.raises(ValueError, match=reason):
        holdout.validate_registry(payload)


def test_partial_preparation_scores_only_observed_letters(tmp_path: Path, registry: Path) -> None:
    root = tmp_path / "run"
    with model_frameworks_blocked():
        result = holdout.prepare(registry, root)
        assert result["included_samples"] == 8
        assert holdout.prepare(registry, root)["files_written"] == 0
        _, prepared, manifest = holdout.load_prepared(root)
    assert len(manifest["excluded"]) == 200
    assert prepared["expected_samples"] == 208
    rows = {
        item["sample_id"]: {**item, "prediction": item["truth"]} for item in manifest["samples"]
    }
    score = holdout.score(manifest, rows)
    assert score["total"] == 8 and score["accuracy"] == 1
    assert score["by_writer"]["W06"]["samples"] == 2


@pytest.mark.parametrize("target", ["source", "crop", "coordinates", "manifest", "roles"])
def test_prepared_holdout_refuses_changed_evidence(
    tmp_path: Path, registry: Path, target: str
) -> None:
    root = tmp_path / "run"
    holdout.prepare(registry, root)
    _, _, manifest = holdout.load_prepared(root)
    paths = {
        "source": root / manifest["sources"][0]["copied_path"],
        "crop": root / manifest["samples"][0]["crop_path"],
        "coordinates": root / holdout.COORDINATES_NAME,
        "manifest": root / holdout.MANIFEST_NAME,
        "roles": root / holdout.ROLES_NAME,
    }
    path = paths[target]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        holdout.load_prepared(root)


@pytest.fixture()
def candidate(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    root = tmp_path / "adaptation"
    checkpoint = root / "training/best"
    checkpoint.mkdir(parents=True)
    inventory = {"layer.weight": {"shape": [1], "dtype": "float32"}}
    header = json.dumps(
        {"model.layer.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
    ).encode()
    weights = struct.pack("<Q", len(header)) + header + struct.pack("<f", 1.0)
    (checkpoint / "state.safetensors").write_bytes(weights)
    digest = hashlib.sha256(weights).hexdigest()
    write_json(
        checkpoint / "state.json",
        {
            "status": "pass_complete",
            "weights_only": True,
            "pass_index": 2,
            "candidate": "adapted",
            "tensors_sha256": digest,
            "trainable_inventory": inventory,
            "source_state_sha256": "c" * 64,
        },
    )
    baseline = {
        "checkpoint_sha256": "b" * 64,
        "inference": dict(frozen.INFERENCE),
        "initialization": {"trainable_inventory": inventory},
    }
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    write_json(
        pilot / "report.json",
        {
            "data": {
                "source_images": [
                    {"writer_id": f"W{index:02d}", "source_sha256": f"{index:064x}"}
                    for index in range(1, 6)
                ]
            }
        },
    )
    write_json(pilot / "crop-manifest.json", {"inert": "development manifest"})
    write_json(
        root / "adaptation-report.json",
        {
            "version": "penforge-volunteer-adaptation-report-v1",
            "status": "training_complete_candidate_selected",
            "candidate": "adapted",
            "product_qualification": "none",
            "test_evaluated": False,
            "development_writers": ["W01", "W02", "W03", "W04", "W05"],
            "data": {
                "frozen_winner": {"checkpoint_sha256": "b" * 64},
                "pilot": {
                    "run": str(pilot),
                    "report_sha256": hashlib.sha256(
                        (pilot / "report.json").read_bytes()
                    ).hexdigest(),
                    "crop_manifest_sha256": hashlib.sha256(
                        (pilot / "crop-manifest.json").read_bytes()
                    ).hexdigest(),
                },
            },
            "selection": {
                "status": "candidate_selected_on_validation",
                "selected_pass": 2,
                "forgetting_floor_correct": 1348,
                "selected_validation_correct": 1353,
                "selected_checkpoint": digest,
                "selected_source_checkpoint": "c" * 64,
            },
        },
    )
    return root, baseline


def test_candidate_verification_rejects_wrong_weights_or_ancestry(candidate) -> None:
    root, baseline = candidate
    with model_frameworks_blocked():
        result = holdout_evaluate.verify_candidate(root, baseline)
    assert result["pass_index"] == 2
    changed = {**baseline, "checkpoint_sha256": "d" * 64}
    with pytest.raises(ValueError, match="wrong_ancestry"):
        holdout_evaluate.verify_candidate(root, changed)
    path = root / "training/best/state.safetensors"
    path.write_bytes(path.read_bytes()[:-1] + b"x")
    with pytest.raises(ValueError, match="disagrees_with_selection"):
        holdout_evaluate.verify_candidate(root, baseline)


def test_preparation_and_model_verification_use_the_strict_guard_in_a_fresh_process(
    tmp_path: Path, registry: Path, candidate
) -> None:
    adapted, baseline = candidate
    run = tmp_path / "isolated-holdout"
    code = (
        "import sys\nfrom pathlib import Path\n"
        "from research.english.volunteer.prepare import model_frameworks_blocked\n"
        "with model_frameworks_blocked():\n"
        " from research.english.volunteer import holdout, holdout_evaluate\n"
        f" holdout.prepare(Path({str(registry)!r}), Path({str(run)!r}))\n"
        f" baseline = {baseline!r}\n"
        f" plan = holdout_evaluate.prepare_plan(Path({str(run)!r}), "
        f"adaptation_run=Path({str(adapted)!r}), frozen_verifier=lambda _: baseline)\n"
        " assert plan['included_samples'] == 8\n"
        " assert not {'torch', 'transformers', 'safetensors'} & set(sys.modules)\n"
        " print('strict_guard_verified')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "strict_guard_verified"


@pytest.mark.parametrize("change", ["failed_guard", "optimizer_state", "wrong_dtype"])
def test_candidate_must_pass_guard_and_contain_only_matching_model_tensors(
    candidate, change
) -> None:
    root, baseline = candidate
    report_path = root / "adaptation-report.json"
    report = json.loads(report_path.read_text())
    if change == "failed_guard":
        report["selection"]["selected_validation_correct"] = 1347
        write_json(report_path, report)
    else:
        weights_path = root / "training/best/state.safetensors"
        raw = weights_path.read_bytes()
        length = struct.unpack("<Q", raw[:8])[0]
        header = json.loads(raw[8 : 8 + length])
        body = raw[8 + length :]
        if change == "optimizer_state":
            header["optimizer.step"] = {"dtype": "F32", "shape": [1], "data_offsets": [4, 8]}
            body += struct.pack("<f", 1.0)
        else:
            header["model.layer.weight"]["dtype"] = "I32"
        encoded = json.dumps(header).encode()
        weights_path.write_bytes(struct.pack("<Q", len(encoded)) + encoded + body)
        digest = hashlib.sha256(weights_path.read_bytes()).hexdigest()
        state_path = root / "training/best/state.json"
        state = json.loads(state_path.read_text())
        state["tensors_sha256"] = digest
        report["selection"]["selected_checkpoint"] = digest
        write_json(state_path, state)
        write_json(report_path, report)
    with model_frameworks_blocked(), pytest.raises(ValueError):
        holdout_evaluate.verify_candidate(root, baseline)


def test_plan_refuses_a_development_page(tmp_path: Path, registry: Path, candidate) -> None:
    root = tmp_path / "run"
    holdout.prepare(registry, root)
    adapted, baseline = candidate
    report_path = adapted / "adaptation-report.json"
    report = json.loads(report_path.read_text())
    pilot_path = Path(report["data"]["pilot"]["run"]) / "report.json"
    pilot = json.loads(pilot_path.read_text())
    pilot["data"]["source_images"][0]["source_sha256"] = json.loads(registry.read_text())[
        "writers"
    ][0]["source_sha256"]
    write_json(pilot_path, pilot)
    report["data"]["pilot"]["report_sha256"] = hashlib.sha256(pilot_path.read_bytes()).hexdigest()
    write_json(report_path, report)
    with model_frameworks_blocked(), pytest.raises(ValueError, match="overlaps_training"):
        holdout_evaluate.prepare_plan(
            root, adaptation_run=adapted, frozen_verifier=lambda _: baseline
        )
    assert not (root / holdout_evaluate.PLAN_NAME).exists()


def test_preparation_cannot_change_the_frozen_inventory(tmp_path: Path, registry: Path) -> None:
    root = tmp_path / "run"
    holdout.prepare(registry, root)
    before = {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    payload = json.loads(registry.read_text())
    payload["writers"][0]["boxes"][0]["x0"] += 1
    write_json(registry, payload)
    with pytest.raises(FileExistsError, match="already_frozen"):
        holdout.prepare(registry, root)
    assert before == {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def test_holdout_cli_refuses_owner_stage_before_data_or_frameworks() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "research.english.volunteer_run", "holdout-evaluate"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "requires_owner_run" in result.stderr


def test_owner_gate_precedes_all_work(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires_owner_run"):
        holdout_evaluate.run(tmp_path / "absent")


def prepare_pair(root: Path, registry: Path, candidate) -> tuple[dict[str, Any], Any]:
    adapted, baseline = candidate
    holdout.prepare(registry, root)

    def prepare(run: Path, **kwargs):
        return holdout_evaluate.prepare_plan(
            run, adaptation_run=adapted, frozen_verifier=lambda _: baseline
        )

    with model_frameworks_blocked():
        plan = prepare(root)
    return plan, prepare


@pytest.mark.parametrize("fail_at", [None, "baseline", "adapted"])
def test_complete_paired_flow_and_one_time_claim(
    tmp_path: Path, registry: Path, candidate, fail_at
) -> None:
    root = tmp_path / "run"
    plan, prepare = prepare_pair(root, registry, candidate)
    assert plan["included_samples"] == 8
    calls: list[str] = []

    class Torch:
        @staticmethod
        def manual_seed(seed):
            assert seed == frozen.INFERENCE["seed"]

    def generator(torch, model, tokenizer, processor, samples, run, **kwargs):
        label = "adapted" if "apply" in calls else "baseline"
        calls.append(label)
        if fail_at == label:
            raise RuntimeError("synthetic interruption")
        assert kwargs["batch_size"] == 8 and kwargs["max_new_tokens"] == 8
        records = []
        for index, sample in enumerate(samples):
            with Image.open(run / sample.crop_path) as image:
                assert image.size == (384, 384)
            prediction = sample.truth
            if label == "baseline" and index == 0:
                prediction = sample.truth.lower()
            if label == "adapted" and index == 1:
                prediction += " "
            records.append(
                {
                    "sample_id": sample.sample_id,
                    "writer_id": sample.writer_id,
                    "truth": sample.truth,
                    "prediction": prediction,
                }
            )
        kwargs["progress"](json.dumps({"completed": len(records), "total": len(records)}))
        return records, {"generation_limit_hits": 0}

    lines: list[str] = []

    def execute():
        return holdout_evaluate.run(
            root,
            owner_run=True,
            adaptation_run=candidate[0],
            plan_preparer=prepare,
            torch_factory=lambda _: Torch(),
            model_loader=lambda *_: (object(), None, None, {}),
            candidate_applier=lambda *_: calls.append("apply"),
            generator=generator,
            progress=lines.append,
        )

    with model_frameworks_blocked():
        if fail_at:
            with pytest.raises(RuntimeError, match="synthetic interruption"):
                execute()
            claim = json.loads((root / holdout_evaluate.CLAIM_NAME).read_text())
            assert claim["status"].startswith("stopped")
            assert (root / "baseline-predictions-private.jsonl").exists() == (fail_at == "adapted")
        else:
            report = execute()
            assert report["status"] == "paired_external_evaluation_complete"
            assert report["samples"] == 8
            assert report["baseline_correct"] == report["adapted_correct"] == 7
            assert report["fixes"] == report["regressions"] == 1
            assert calls == ["baseline", "apply", "adapted"]
            assert [json.loads(line)["model"] for line in lines] == ["baseline", "adapted"]
            assert report["training_performed"] is False
        with pytest.raises(FileExistsError, match="already_claimed"):
            execute()


def test_changed_candidate_refuses_before_model_setup(
    tmp_path: Path, registry: Path, candidate
) -> None:
    root = tmp_path / "run"
    _, prepare = prepare_pair(root, registry, candidate)
    path = candidate[0] / "adaptation-report.json"
    path.write_bytes(path.read_bytes() + b" ")

    def forbidden(seed):
        raise AssertionError("model setup must not run")

    with pytest.raises(FileExistsError, match="existing_output_differs"):
        holdout_evaluate.run(root, owner_run=True, plan_preparer=prepare, torch_factory=forbidden)
    assert not (root / holdout_evaluate.CLAIM_NAME).exists()
