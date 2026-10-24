"""Mechanical R6 unit checks with synthetic tensors. No pretrained model is involved."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
import torch
from test_english_r6_model_fixtures import (
    CLASSES,
    REPO_ROOT,
    build_case,
    build_plan,
    fake_verifier,
)

from research.english.r6_model import checkpoint, experiment, progress, rows
from research.english.r6_model.budgets import (
    BudgetExceeded,
    BudgetPlan,
    Consumption,
    RunBudget,
    assert_publication_fits,
    cpu_limit_to_install,
    directory_bytes,
    publication_peak_bytes,
)
from research.english.r6_model.initialization import ExpectedHashes
from research.english.r6_model.recipe import (
    MAX_AGGREGATE_PASSES,
    R6ModelRecipe,
    data_epoch,
    load_recipe,
    recipe_hash,
)

LINEAGE = checkpoint.StateLineage(
    lineage_hash="1" * 64,
    recipe_hash="2" * 64,
    base_inventory_sha256="3" * 64,
    delta_sha256="4" * 64,
    candidate="r6_continuation_r5_pass3",
)


def tiny_trainable(seed: int = 0) -> list[tuple[str, torch.nn.Parameter]]:
    torch.manual_seed(seed)
    model = torch.nn.Sequential(torch.nn.Linear(4, 4), torch.nn.Linear(4, 4))
    return [
        (f"layer{index}.{name}", parameter)
        for index, (name, parameter) in enumerate(model.named_parameters())
    ]


def stepped_optimizer(named) -> torch.optim.AdamW:
    optimizer = torch.optim.AdamW([parameter for _, parameter in named], lr=1e-3)
    total = sum(parameter.sum() for _, parameter in named)
    total.backward()
    optimizer.step()
    return optimizer


def commit(pass_index: int, *, accuracy: float, epoch: int | None = None) -> dict:
    return {
        "pass_index": pass_index,
        "data_epoch": epoch if epoch is not None else 3 + pass_index,
        "accuracy": accuracy,
        "correct": int(round(accuracy * 4)),
        "n": 4,
        "scalar_cer": 1.0 - accuracy,
        "per_crop_ms_p50": 10.0 + pass_index,
        "checkpoint_sha256": f"{pass_index}" * 64,
        "predictions_sha256": "9" * 64,
        "samples": 8,
        "optimizer_steps": 2,
        "training_seconds": 1.5,
        "validation_seconds": 0.5,
    }


def publish(output: Path, named, optimizer, *, pass_index: int, passes) -> checkpoint.TrainingState:
    return checkpoint.publish_latest(
        output,
        model_named=named,
        optimizer=optimizer,
        torch=torch,
        python_random=random.Random(7),
        lineage=LINEAGE,
        pass_index=pass_index,
        data_epoch=3 + pass_index,
        optimizer_step=pass_index,
        passes=passes,
        consumption=Consumption(wall_seconds=10.0 * pass_index, cpu_seconds=12.0 * pass_index),
    )


def test_pinned_recipe_matches_its_config_file():
    recipe = load_recipe()
    raw = json.loads(
        (REPO_ROOT / "research/configs/english-r6-model-continuation.json").read_text()
    )
    assert recipe.model_dump() == raw
    assert recipe.pass_limit == 2
    assert recipe.initialization == "verified_r4_base_plus_r5_selected_delta"
    assert recipe.optimizer_initialization == "fresh_adamw_no_r5_optimizer_state"
    assert recipe.trocr_learning_rate == 8e-6
    assert recipe.data_epoch_offset == 3
    assert recipe.early_stopping_patience == 1
    assert recipe.test_policy == "part_a_owned_final_test_separately_invoked"
    assert recipe_hash(recipe) == recipe_hash(load_recipe())


def test_recipe_rejects_out_of_scope_design():
    raw = load_recipe().model_dump()
    raw["pass_limit"] = MAX_AGGREGATE_PASSES + 1
    with pytest.raises(ValueError):
        R6ModelRecipe.model_validate(raw)
    raw = load_recipe().model_dump()
    raw["trocr_learning_rate"] = 0.00005
    with pytest.raises(ValueError):
        R6ModelRecipe.model_validate(raw)
    raw = load_recipe().model_dump()
    raw["optimizer_initialization"] = "resume_r5_optimizer"
    with pytest.raises(ValueError):
        R6ModelRecipe.model_validate(raw)
    raw = load_recipe().model_dump()
    raw["interrupted_attempt_charge_seconds"] = 60
    with pytest.raises(ValueError):
        R6ModelRecipe.model_validate(raw)


def test_data_epoch_continues_after_r5_passes():
    recipe = load_recipe()
    assert [data_epoch(recipe, index) for index in (1, 2)] == [4, 5]
    with pytest.raises(ValueError):
        data_epoch(recipe, 3)


def test_publication_peak_arithmetic_counts_two_full_and_two_model_only_states():
    # R5 measured 336,064,512 tensor bytes for the trainable subset; the file adds a header.
    full_state = 1_008_200_832
    selected_state = 336_071_552
    peak = publication_peak_bytes(full_state_bytes=full_state, selected_state_bytes=selected_state)
    assert peak == 2 * full_state + 2 * selected_state
    # The pinned standalone recipe (2.6 GB) cannot hold the real publication order.
    assert peak > 2_600_000_000
    assert peak < 3_000_000_000  # the experiment's shared ceiling and Part A's derived recipe
    # Replacing either retained model-only state with a full optimizer state misses the ceiling.
    assert 2 * full_state + 2 * full_state > 3_000_000_000
    with pytest.raises(ValueError):
        publication_peak_bytes(full_state_bytes=0, selected_state_bytes=selected_state)


def test_real_inventory_refuses_the_pinned_2_6gb_recipe_and_fits_the_3gb_derived_recipe():
    state_path = REPO_ROOT / "research/runs/r5-agent-authorized-20261021/training/best/state.json"
    if not state_path.is_file():
        pytest.skip("private R5 delta metadata is absent")
    inventory = json.loads(state_path.read_text())["trainable_inventory"]
    assert len(inventory) == 53
    full = checkpoint.estimate_full_state_bytes(inventory)
    selected = checkpoint.estimate_selected_state_bytes(inventory)
    estimated = checkpoint.estimated_publication_peak(inventory)
    # The estimate must not understate a real full or selected state.
    assert full > 1_008_193_536 and selected > 336_064_512
    assert estimated == 2 * full + 2 * selected == 2_690_917_368
    pinned = load_recipe()
    assert pinned.max_output_bytes == 2_600_000_000
    # The unchanged pinned recipe is refused at preflight for the real inventory.
    with pytest.raises(ValueError, match="publication_peak_exceeds_output_ceiling"):
        assert_publication_fits(estimated, BudgetPlan.from_recipe(pinned))
    # Part A's resource-only derived recipe raises the ceiling to 3 GB and fits with headroom.
    derived = R6ModelRecipe.model_validate(
        {**pinned.model_dump(), "max_output_bytes": 3_000_000_000}
    )
    assert_publication_fits(estimated, BudgetPlan.from_recipe(derived))
    assert estimated + 10_000_000 < 3_000_000_000


def test_publication_never_removes_the_only_recoverable_state(tmp_path, monkeypatch):
    output = tmp_path / "run"
    output.mkdir()
    named = tiny_trainable()
    optimizer = stepped_optimizer(named)
    first = publish(output, named, optimizer, pass_index=1, passes=[commit(1, accuracy=0.5)])
    observed: dict[str, bool] = {}
    original = checkpoint.save_state

    def spy(directory: Path, **kwargs):
        observed["previous_latest_present"] = (output / "latest").is_dir()
        return original(directory, **kwargs)

    monkeypatch.setattr(checkpoint, "save_state", spy)
    with torch.no_grad():
        named[0][1].add_(1.0)
    second = publish(
        output,
        named,
        optimizer,
        pass_index=2,
        passes=[commit(1, accuracy=0.5), commit(2, accuracy=0.75)],
    )
    assert observed["previous_latest_present"] is True
    assert second.tensors_sha256 != first.tensors_sha256
    state = checkpoint.TrainingState.model_validate_json(
        (output / "latest" / "state.json").read_text()
    )
    assert [item.pass_index for item in state.passes] == [1, 2]
    assert state.budget.wall_seconds == 20.0
    assert not (output / "latest.old").exists()
    assert not (output / "latest.new").exists()


def test_rotation_helpers_reclaim_leftovers_but_keep_the_candidate(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    for name in ("latest.new", "latest.old", "best.old"):
        (output / name).mkdir()
    (output / "selected-candidate").mkdir()
    removed = checkpoint.clean_rotation_leftovers(output)
    assert sorted(removed) == ["best.old", "latest.new", "latest.old"]
    assert not (output / "latest.new").exists()
    assert (output / "selected-candidate").is_dir()
    assert checkpoint.discard_selected_candidate(output) is True
    assert not (output / "selected-candidate").exists()
    assert checkpoint.discard_selected_candidate(output) is False


def test_selected_winner_is_retained_until_the_new_one_is_promoted(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    named = tiny_trainable()
    states = []
    for pass_index in (1, 2):
        with torch.no_grad():
            named[0][1].add_(0.5)
        state = checkpoint.write_selected_candidate(
            output,
            model_named=named,
            lineage=LINEAGE,
            pass_index=pass_index,
            data_epoch=3 + pass_index,
            source_state_sha256=str(pass_index) * 64,
        )
        states.append(state)
        assert (output / "selected-candidate").is_dir()
        if pass_index == 2:
            # The previous winner is still readable before promotion.
            previous, _ = checkpoint.load_selected(output / "best")
            assert previous
        checkpoint.promote_selected(output)
        retained, selected = checkpoint.load_selected(output / "best")
        assert retained and selected.tensors_sha256 == state.tensors_sha256
        assert not (output / "best.old").exists()
        assert not (output / "selected-candidate").exists()
    assert states[0].tensors_sha256 != states[1].tensors_sha256


def test_publication_peak_covers_the_two_full_and_two_selected_coexistence_window(
    tmp_path, monkeypatch
):
    """Prove ``latest`` + ``latest.new`` + ``best`` + ``selected-candidate`` coexist.

    This is the real run order: a pass writes its model-only candidate first (the previous
    winner must stay readable) and then commits the full state, so the peak is two full
    states plus two model-only states at the same instant.
    """
    output = tmp_path / "run"
    output.mkdir()
    named = tiny_trainable()
    optimizer = stepped_optimizer(named)
    inventory = {
        name: checkpoint.ParameterRecord(
            dtype=str(parameter.dtype).removeprefix("torch."),
            shape=[int(size) for size in parameter.shape],
        )
        for name, parameter in named
    }
    full = checkpoint.estimate_full_state_bytes(inventory)
    selected = checkpoint.estimate_selected_state_bytes(inventory)
    peak = publication_peak_bytes(full_state_bytes=full, selected_state_bytes=selected)
    assert peak == 2 * full + 2 * selected

    # Pass 1 commits the retained winner.
    publish(output, named, optimizer, pass_index=1, passes=[commit(1, accuracy=0.5)])
    checkpoint.write_selected_candidate(
        output,
        model_named=named,
        lineage=LINEAGE,
        pass_index=1,
        data_epoch=4,
        source_state_sha256="1" * 64,
    )
    checkpoint.promote_selected(output)

    # Pass 2 writes its candidate before the full-state commit, as the run does.
    with torch.no_grad():
        named[0][1].add_(1.0)
    checkpoint.write_selected_candidate(
        output,
        model_named=named,
        lineage=LINEAGE,
        pass_index=2,
        data_epoch=5,
        source_state_sha256="2" * 64,
    )

    observed: list[dict] = []
    original = checkpoint.save_state

    def spy(directory: Path, **kwargs):
        state = original(directory, **kwargs)
        observed.append(
            {
                "bytes": directory_bytes(output),
                "latest": (output / "latest").is_dir(),
                "latest_new": (output / "latest.new").is_dir(),
                "best": (output / "best").is_dir(),
                "selected_candidate": (output / "selected-candidate").is_dir(),
            }
        )
        return state

    monkeypatch.setattr(checkpoint, "save_state", spy)
    second = publish(
        output,
        named,
        optimizer,
        pass_index=2,
        passes=[commit(1, accuracy=0.5), commit(2, accuracy=0.75)],
    )
    assert second is not None
    assert len(observed) == 1
    window = observed[0]
    assert all(window[key] for key in ("latest", "latest_new", "best", "selected_candidate")), (
        window
    )
    # The measured coexistence window stays inside the estimate that counts all four states.
    assert window["bytes"] <= peak
    # The old two-full-plus-one-selected estimate would have missed this retained winner.
    assert peak == (2 * full + selected) + selected


def test_resume_rejects_drifted_lineage_recipe_initialization_and_epochs(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    named = tiny_trainable()
    optimizer = stepped_optimizer(named)
    publish(output, named, optimizer, pass_index=1, passes=[commit(1, accuracy=0.5)])
    _, state = checkpoint.load_state(output / "latest")
    inventory = {name: parameter for name, parameter in named}
    good = checkpoint.verify_resume(
        state,
        lineage=LINEAGE,
        trainable_inventory={
            name: checkpoint.ParameterRecord(
                dtype=str(parameter.dtype).removeprefix("torch."),
                shape=[int(size) for size in parameter.shape],
            )
            for name, parameter in inventory.items()
        },
        pass_limit=2,
        data_epochs=[4],
    )
    assert good.wall_seconds == 10.0 and good.cpu_seconds == 12.0

    def records():
        return {
            name: checkpoint.ParameterRecord(
                dtype=str(parameter.dtype).removeprefix("torch."),
                shape=[int(size) for size in parameter.shape],
            )
            for name, parameter in named
        }

    assert records()
    with pytest.raises(ValueError):
        checkpoint.verify_resume(
            state,
            lineage=checkpoint.StateLineage(
                lineage_hash="9" * 64,
                recipe_hash=LINEAGE.recipe_hash,
                base_inventory_sha256=LINEAGE.base_inventory_sha256,
                delta_sha256=LINEAGE.delta_sha256,
                candidate=LINEAGE.candidate,
            ),
            trainable_inventory=records(),
            pass_limit=2,
            data_epochs=[4],
        )
    with pytest.raises(ValueError):
        checkpoint.verify_resume(
            state,
            lineage=LINEAGE,
            trainable_inventory=records(),
            pass_limit=2,
            data_epochs=[5],
        )
    with pytest.raises(ValueError):
        checkpoint.verify_resume(
            state, lineage=LINEAGE, trainable_inventory=records(), pass_limit=1, data_epochs=[4]
        )
    extra = records()
    extra["layer0.extra"] = checkpoint.ParameterRecord(dtype="float32", shape=[1])
    with pytest.raises(ValueError):
        checkpoint.verify_resume(
            state, lineage=LINEAGE, trainable_inventory=extra, pass_limit=2, data_epochs=[4]
        )


def test_state_rejects_non_contiguous_committed_passes(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    named = tiny_trainable()
    optimizer = stepped_optimizer(named)
    with pytest.raises(ValueError):
        publish(output, named, optimizer, pass_index=1, passes=[commit(2, accuracy=0.5)])
    # Nothing was rotated into place, so no resumable state claims this pass.
    assert not (output / "latest").exists()
    assert not (output / "latest" / "state.json").exists()


def test_progress_is_flushed_json_and_refuses_private_fields(monkeypatch):
    lines: list[str] = []
    sink = progress.Progress(writer=lines.append, clock=lambda: 0.0)
    sink.event("r6-train", candidate="c", pass_index=1, completed_steps=1, total_steps=2)
    payload = json.loads(lines[0])
    assert payload["stage"] == "r6-train"
    assert payload["completed_steps"] == 1 and payload["total_steps"] == 2
    for field in ("label", "truth", "prediction", "text"):
        with pytest.raises(ValueError):
            sink.event("r6-train", **{field: "A"})
    with pytest.raises(ValueError):
        sink.event("r6-train", note="x" * 201)

    captured: dict[str, object] = {}

    def fake_print(line, **kwargs):
        captured["line"] = line
        captured["kwargs"] = kwargs

    monkeypatch.setattr("builtins.print", fake_print)
    progress.Progress(clock=lambda: 0.0).event("r6-preflight", status="ok")
    assert captured["kwargs"] == {"flush": True}
    assert json.loads(str(captured["line"]))["status"] == "ok"


def test_cumulative_budget_refuses_work_the_ceiling_cannot_finish(tmp_path):
    recipe = load_recipe()
    plan = BudgetPlan.from_recipe(
        recipe, prior=Consumption(wall_seconds=11_500, cpu_seconds=20_000)
    )
    assert plan.remaining_wall_seconds == pytest.approx(200)
    clock = [0.0]
    budget = RunBudget(
        plan,
        tmp_path,
        clock=lambda: clock[0],
        cpu_clock=lambda: 0.0,
        rss_mb=lambda: 0.0,
        disk_bytes=lambda path: 0,
    )
    with pytest.raises(BudgetExceeded):
        budget.require_wall(300.0, reason="pass-2")
    budget.require_wall(100.0, reason="pass-2")
    clock[0] = 250.0
    with pytest.raises(BudgetExceeded):
        budget.checkpoint()
    assert budget.consumption().wall_seconds == pytest.approx(11_750)


def test_budget_checks_cpu_rss_and_disk(tmp_path):
    recipe = load_recipe()
    plan = BudgetPlan.from_recipe(recipe)
    cpu = [0.0]
    budget = RunBudget(
        plan,
        tmp_path,
        clock=lambda: 0.0,
        cpu_clock=lambda: cpu[0],
        rss_mb=lambda: 0.0,
        disk_bytes=lambda path: 0,
    )
    budget.checkpoint()
    cpu[0] = recipe.max_cpu_seconds + 1
    with pytest.raises(BudgetExceeded):
        budget.checkpoint()
    heavy = RunBudget(
        plan,
        tmp_path,
        clock=lambda: 0.0,
        cpu_clock=lambda: 0.0,
        rss_mb=lambda: recipe.max_rss_mb + 1,
        disk_bytes=lambda path: 0,
    )
    with pytest.raises(BudgetExceeded):
        heavy.checkpoint()
    full = RunBudget(
        plan,
        tmp_path,
        clock=lambda: 0.0,
        cpu_clock=lambda: 0.0,
        rss_mb=lambda: 0.0,
        disk_bytes=lambda path: recipe.max_output_bytes + 1,
    )
    with pytest.raises(BudgetExceeded):
        full.checkpoint()
    assert cpu_limit_to_install(100.0, None) == 100.0
    assert cpu_limit_to_install(100.0, 40.0) == 40.0
    with pytest.raises(ValueError):
        cpu_limit_to_install(0.0, None)


def test_rows_reject_test_rows_extra_fields_and_duplicates(tmp_path):
    case = build_case(tmp_path / "case")
    study = rows.load_study(case["staging"])
    assert len(study.train) == len(CLASSES) == 52
    assert len(study.validation) == 52
    assert set(row["label"] for row in study.train) == set(CLASSES)

    original = [
        json.loads(line)
        for line in (case["staging"] / "train-validation.jsonl").read_text().splitlines()
    ]

    def write(name: str, value: list[dict]) -> None:
        (case["staging"] / name).write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in value),
            encoding="utf-8",
        )

    test_row = dict(original[0])
    test_row["split"] = "test"
    write("with-test.jsonl", [*original, test_row])
    with pytest.raises(ValueError):
        rows.load_study(case["staging"], "with-test.jsonl")

    extra = dict(original[0])
    extra["id"] = "r6-extra"
    extra["note"] = "unexpected"
    write("with-extra.jsonl", [*original, extra])
    with pytest.raises(ValueError):
        rows.load_study(case["staging"], "with-extra.jsonl")

    duplicate = dict(original[1])
    write("with-duplicate.jsonl", [*original, duplicate])
    with pytest.raises(ValueError):
        rows.load_study(case["staging"], "with-duplicate.jsonl")

    leaked = dict(original[0])
    leaked["split"] = "validation"
    write("with-leakage.jsonl", [*original, leaked])
    with pytest.raises(ValueError):
        rows.load_study(case["staging"], "with-leakage.jsonl")

    changed = dict(original[0])
    changed["hash"] = "0" * 64
    changed["path"] = f"images/{'0' * 64}.png"
    write("with-missing-image.jsonl", [*original, changed])
    with pytest.raises(ValueError):
        rows.load_study(case["staging"], "with-missing-image.jsonl")


def test_preflight_refuses_output_inside_the_frozen_r5_run(tmp_path):
    """The fixture's ``r5-run`` directory is the same boundary Part A passes as ``r5_run``.

    Part A protects that directory because it holds ``winner-frozen.json`` and all R5
    evidence; R6 must refuse both the directory itself and any output nested inside it,
    while still accepting a sibling directory.
    """
    case = build_case(tmp_path / "case")
    assert case["winner"] == case["r5_run"] / "winner-frozen.json"
    expected = ExpectedHashes(
        winner_sha256=case["expected_winner_sha256"],
        delta_sha256=case["expected_delta_sha256"],
    )
    for output in (case["r5_run"], case["r5_run"] / "r6-training", case["staging"] / "run"):
        plan = build_plan(case, output)
        with pytest.raises(ValueError, match="separate_from_inputs"):
            experiment.preflight(
                plan, bundle_verifier=fake_verifier, expected_initialization=expected
            )

    accepted = experiment.preflight(
        build_plan(case, case["root"] / "r6-training"),
        bundle_verifier=fake_verifier,
        expected_initialization=expected,
    )
    assert accepted.lineage["test_access"] == "not_requested_by_part_b"
    assert accepted.plan.output.parent == case["root"].resolve()
    assert not accepted.plan.output.exists()


def test_plan_rejects_a_recipe_outside_the_pinned_initialization(tmp_path):
    case = build_case(tmp_path / "case")
    raw = json.loads(case["recipe"].read_text())
    raw["initialization"] = "verified_r4_selected_checkpoint"
    mutated = tmp_path / "mutated-recipe.json"
    mutated.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        build_plan(case, tmp_path / "run", recipe=mutated)
    plan = build_plan(case, tmp_path / "run")
    assert plan.recipe.initialization == "verified_r4_base_plus_r5_selected_delta"
    assert fake_verifier(plan.model_bundle)["scope"] == "fixture"
