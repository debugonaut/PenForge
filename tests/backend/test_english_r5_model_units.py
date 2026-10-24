"""Mechanical R5 model-unit checks. No pretrained model is imported or instantiated.

The only Torch objects here are a 3x3 ``Linear`` used to exercise checkpoint tensors and
a synthetic tokenizer used to prove label/row alignment. No neural network, weights or
training run is involved.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
from pathlib import Path

import pytest
import torch
from PIL import Image

from research.english.r5_model import (
    augment,
    batching,
    checkpoint,
    evaluate,
    preprocess,
    rows,
    selection,
)
from research.english.r5_model.budgets import (
    BudgetExceeded,
    BudgetPlan,
    RunBudget,
    cpu_limit_to_install,
)
from research.english.r5_model.recipe import R5ModelRecipe, load_recipe, recipe_hash
from research.english.r5_model.scheduler import learning_rate_multiplier

CLASSES = rows.CLASSES
PNG_SIZE = (128, 128)


def png_bytes(salt: int, size: tuple[int, int] = PNG_SIZE) -> bytes:
    image = Image.new("L", size, 255)
    image.putpixel((salt % size[0], (salt * 7) % size[1]), 0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def pixel_hash(data: bytes) -> str:
    with Image.open(io.BytesIO(data)) as image:
        gray = image.convert("L")
        return hashlib.sha256(gray.tobytes()).hexdigest()


def sample_row(
    staging: Path,
    *,
    sample_id: str,
    label: str,
    writer: str,
    split: str,
    salt: int,
) -> dict:
    data = png_bytes(salt)
    digest = hashlib.sha256(data).hexdigest()
    images = staging / "images"
    images.mkdir(parents=True, exist_ok=True)
    (images / f"{digest}.png").write_bytes(data)
    return {
        "id": sample_id,
        "hash": digest,
        "path": f"images/{digest}.png",
        "pixel_hash": pixel_hash(data),
        "label": label,
        "writer": writer,
        "source_group": f"nist-page-{salt}",
        "source_partition": 0,
        "split": split,
        "source_member": f"by_write/hsf_0/w{salt}/u{salt}_{salt}.png",
    }


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    path = tmp_path / "stage"
    path.mkdir()
    return path


def test_pinned_recipe_matches_its_config_file():
    recipe = load_recipe()
    raw = json.loads(
        (Path(__file__).resolve().parents[2] / "research/configs/english-r5-model.json").read_text()
    )
    assert recipe.model_dump() == raw
    assert recipe.initialization == "verified_r4_selected_checkpoint"
    assert recipe.pass_limit == 3
    assert recipe.early_stopping_patience == 1
    assert recipe.test_policy == "part_a_owned_final_test_separately_invoked"
    assert recipe_hash(recipe) == recipe_hash(load_recipe())


def test_recipe_rejects_out_of_scope_design(tmp_path):
    raw = load_recipe().model_dump()
    raw["pass_limit"] = 9
    with pytest.raises(ValueError):
        R5ModelRecipe.model_validate(raw)
    raw = load_recipe().model_dump()
    raw["augmentation"] = "elastic_and_flip"
    with pytest.raises(ValueError):
        R5ModelRecipe.model_validate(raw)
    raw = load_recipe().model_dump()
    raw["max_run_wall_seconds"] = 60
    with pytest.raises(ValueError):
        R5ModelRecipe.model_validate(raw)


def test_mapping_with_test_rows_is_a_hard_error(staging):
    train = sample_row(staging, sample_id="t1", label="A", writer="w1", split="train", salt=1)
    hidden = sample_row(staging, sample_id="x1", label="b", writer="w2", split="test", salt=2)
    mapping = staging / "study.jsonl"
    mapping.write_text("\n".join(json.dumps(row) for row in (train, hidden)) + "\n")
    with pytest.raises(ValueError, match="split_outside_train_validation"):
        rows.load_study(staging)


def test_sample_fields_are_exactly_the_frozen_contract(staging):
    row = sample_row(staging, sample_id="t1", label="A", writer="w1", split="train", salt=1)
    extra = dict(row, note="not allowed")
    with pytest.raises(ValueError, match="fields_differ"):
        rows.validate_sample(extra, staging)
    missing = {key: value for key, value in row.items() if key != "pixel_hash"}
    with pytest.raises(ValueError, match="fields_differ"):
        rows.validate_sample(missing, staging)


def test_sample_rejects_unsafe_ids_and_changed_bytes(staging):
    row = sample_row(staging, sample_id="t1", label="A", writer="w1", split="train", salt=1)
    unsafe = dict(row, id="../escape")
    with pytest.raises(ValueError):
        rows.validate_sample(unsafe, staging)
    (staging / row["path"]).write_bytes(png_bytes(9))
    with pytest.raises(ValueError, match="content_hash_mismatch"):
        rows.validate_sample(row, staging)


def test_sample_rejects_non_content_addressed_path_and_case_loss(staging):
    row = sample_row(staging, sample_id="t1", label="A", writer="w1", split="train", salt=1)
    with pytest.raises(ValueError, match="content_addressed"):
        rows.validate_sample(dict(row, path="images/other.png"), staging)
    with pytest.raises(ValueError, match="52_classes"):
        rows.validate_sample(dict(row, label="AB"), staging)
    with pytest.raises(ValueError, match="52_classes"):
        rows.validate_sample(dict(row, label="a "), staging)


def test_duplicate_and_writer_overlap_rows_are_rejected(staging):
    first = sample_row(staging, sample_id="t1", label="A", writer="w", split="train", salt=1)
    duplicate_id = sample_row(
        staging, sample_id="t1", label="b", writer="w", split="validation", salt=2
    )
    with pytest.raises(ValueError, match="duplicate_sample_id"):
        rows.normalize_rows([first, duplicate_id], staging)
    other = sample_row(staging, sample_id="v1", label="b", writer="w", split="validation", salt=3)
    with pytest.raises(ValueError, match="writer_overlap"):
        rows.normalize_rows([first, other], staging)


def test_aspect_preserving_pad_does_not_stretch(staging):
    image = Image.new("RGB", (200, 50), (255, 255, 255))
    padded = preprocess.aspect_preserving_pad(image)
    assert padded.size == (384, 384)
    assert padded.getpixel((0, 0)) == (255, 255, 255)
    # 200x50 scaled by 1.92 -> 384x96, centred vertically with white bands.
    assert padded.getpixel((192, 10)) == (255, 255, 255)


def test_augmentation_is_deterministic_bounded_and_orientation_preserving():
    recipe = load_recipe()
    chosen_id = None
    for index in range(20):
        candidate = f"row-{index}"
        if augment.parameters_for(recipe, sample_id=candidate, epoch=2) is not None:
            chosen_id = candidate
            break
    assert chosen_id is not None, "the augmentation probability must select some sample"
    first = augment.parameters_for(recipe, sample_id=chosen_id, epoch=2)
    second = augment.parameters_for(recipe, sample_id=chosen_id, epoch=2)
    assert first == second
    assert first is not None
    assert abs(first.scale - 1.0) <= 0.08
    assert abs(first.angle_degrees) <= 6.0
    coefficients = augment.affine_coefficients(first, (128, 128))
    determinant = coefficients[0] * coefficients[4] - coefficients[1] * coefficients[3]
    assert determinant > 0, "a flip would make the determinant negative"
    unchanged = recipe.model_copy(update={"augmentation_probability": 0.0})
    assert augment.parameters_for(unchanged, sample_id=chosen_id, epoch=1) is None


def test_augmentation_keeps_canvas_and_letter_identity():
    image = Image.new("L", (128, 128), 255)
    image.putpixel((64, 64), 0)
    geometry = augment.Geometry(4.0, 0.05, 1.03, 0.02, -0.02)
    moved = augment.apply(image, geometry)
    assert moved.size == image.size
    assert moved.mode == "L"
    assert moved.getpixel((0, 0)) == 255


def test_epoch_batches_cover_every_row_once_and_change_between_passes():
    items = [{"id": f"r-{index}", "label": "A"} for index in range(11)]
    first = batching.epoch_batches(items, 4, 7, 1)
    second = batching.epoch_batches(items, 4, 7, 2)
    visited = [row["id"] for group in first for row in group]
    assert len(visited) == len(set(visited)) == len(items)
    assert sorted(visited) == sorted(row["id"] for row in items)
    assert [[row["id"] for row in group] for group in first] != [
        [row["id"] for row in group] for group in second
    ]
    assert sum(len(group) for group in first) == len(items)


def test_padded_labels_stay_aligned_with_their_rows():
    class Tokenizer:
        pad_token_id = 1
        eos_token_id = 2
        unk_token_id = 3
        all_special_ids = [0, 1, 2, 3]

        def encode(self, text, add_special_tokens=True, truncation=False):
            return [4 + CLASSES.index(text), 2]

        def decode(self, ids, **kwargs):
            return CLASSES[ids[0] - 4] if 4 <= ids[0] < 56 else ""

    groups = [[{"id": "b", "label": "b"}, {"id": "A", "label": "A"}]]
    labels = batching.padded_label_ids(torch, Tokenizer(), [row["label"] for row in groups[0]], 8)
    assert labels.tolist() == [[4 + 27, 2], [4, 2]]
    assert batching.expected_labels(groups) == ["b", "A"]


def test_learning_rate_schedule_has_declared_endpoints():
    assert learning_rate_multiplier(
        1, total_steps=100, warmup_steps=10, final_ratio=0.2
    ) == pytest.approx(0.1)
    assert learning_rate_multiplier(
        10, total_steps=100, warmup_steps=10, final_ratio=0.2
    ) == pytest.approx(1.0)
    assert learning_rate_multiplier(
        100, total_steps=100, warmup_steps=10, final_ratio=0.2
    ) == pytest.approx(0.2)
    for invalid in (0, 101):
        with pytest.raises(ValueError):
            learning_rate_multiplier(invalid, total_steps=100, warmup_steps=10, final_ratio=0.2)


def pass_result(
    index: int, accuracy: float, cer: float, latency: float | None
) -> selection.PassResult:
    return selection.PassResult(
        pass_index=index,
        accuracy=accuracy,
        correct=round(accuracy * 52),
        n=52,
        scalar_cer=cer,
        per_crop_ms_p50=latency,
        exact_case_sensitive=True,
        checkpoint=f"pass-{index:04d}",
        checkpoint_sha256=f"{index:064d}",
        predictions_sha256=f"{index + 1:064d}",
    )


def test_selection_tie_break_is_accuracy_cer_latency_then_earlier_pass():
    results = [
        pass_result(1, 0.5, 0.5, 10.0),
        pass_result(2, 0.75, 0.25, 30.0),
        pass_result(3, 0.75, 0.25, 20.0),
    ]
    assert selection.select_best(results)["winner"]["pass_index"] == 3
    tied = [pass_result(1, 0.5, 0.5, 10.0), pass_result(2, 0.5, 0.5, 10.0)]
    assert selection.select_best(tied)["winner"]["pass_index"] == 1
    missing = [pass_result(1, 0.5, 0.2, None), pass_result(2, 0.5, 0.2, 99.0)]
    assert selection.select_best(missing)["winner"]["pass_index"] == 2


def test_selection_rejects_inconsistent_metrics():
    bad = pass_result(1, 0.5, 0.5, 1.0).model_copy(update={"correct": 51})
    with pytest.raises(ValueError, match="disagrees"):
        selection.select_best([bad])
    with pytest.raises(ValueError, match="case_sensitive"):
        selection.select_best(
            [pass_result(1, 0.5, 0.5, 1.0).model_copy(update={"exact_case_sensitive": False})]
        )
    different_inventory = pass_result(2, 0.5, 0.5, 1.0).model_copy(update={"n": 51, "correct": 26})
    with pytest.raises(ValueError, match="one_validation_inventory"):
        selection.select_best([pass_result(1, 0.5, 0.5, 1.0), different_inventory])


def test_score_validation_rejects_wrong_counts_and_case_normalisation():
    good = {
        "accuracy": 0.5,
        "correct": 2,
        "n": 4,
        "scalar_cer": 0.25,
        "exact_case_sensitive": True,
    }
    assert evaluate.validate_score(good, samples=4)["accuracy"] == 0.5
    for broken in (
        dict(good, n=5),
        dict(good, correct=3),
        dict(good, exact_case_sensitive=False),
        dict(good, accuracy=1.5),
        {key: value for key, value in good.items() if key != "scalar_cer"},
        dict(good, per_crop_ms_p50=-1),
    ):
        with pytest.raises(ValueError):
            evaluate.validate_score(broken, samples=4)


def test_prediction_records_reject_duplicate_missing_unknown_and_wrong_truth(staging):
    items = [
        sample_row(staging, sample_id="t1", label="A", writer="w", split="train", salt=1),
        sample_row(staging, sample_id="t2", label="b", writer="w", split="train", salt=2),
    ]
    records = evaluate.records_for(items, ["A", "b"])
    assert [record.sample_id for record in records] == ["t1", "t2"]
    evaluate.verify_records(records, items)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate.verify_records([records[0], records[0]], items)
    with pytest.raises(ValueError, match="incomplete"):
        evaluate.verify_records([records[0]], items)
    with pytest.raises(ValueError, match="unknown"):
        evaluate.verify_records(
            [records[0], records[1].model_copy(update={"sample_id": "ghost"})], items
        )
    with pytest.raises(ValueError, match="truth"):
        evaluate.verify_records([records[0].model_copy(update={"truth": "a"}), records[1]], items)


def test_scorer_arity_accepts_one_or_two_argument_callbacks():
    assert evaluate.scorer_arity(lambda records, rows: {}) == 2
    assert evaluate.scorer_arity(lambda records: {}) == 1
    with pytest.raises(ValueError):
        evaluate.scorer_arity(lambda: {})


def test_checkpoint_roundtrip_restores_weights_optimizer_and_rng(tmp_path):
    model = torch.nn.Linear(3, 3)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
    model(torch.ones(1, 3)).sum().backward()
    optimizer.step()
    named = list(model.named_parameters())
    lineage = checkpoint.StateLineage("1" * 64, "2" * 64, "3" * 64, "4" * 64)
    state = checkpoint.save_state(
        tmp_path / "state",
        model_named=named,
        optimizer=optimizer,
        torch=torch,
        python_random=random.Random(5),
        lineage=lineage,
        pass_index=1,
        optimizer_step=1,
    )
    tensors, loaded = checkpoint.load_state(tmp_path / "state")
    assert loaded.model_dump() == state.model_dump()
    assert loaded.trainable_inventory["weight"].shape == [3, 3]
    assert len(tensors) == 4 * len(named)

    clone = torch.nn.Linear(3, 3)
    clone_optimizer = torch.optim.AdamW(clone.parameters(), lr=0.01)
    checkpoint.restore_state(clone, clone_optimizer, tensors, loaded)
    for (name, parameter), (_, original) in zip(
        clone.named_parameters(), model.named_parameters(), strict=True
    ):
        assert torch.equal(parameter, original), name
    assert all("exp_avg" in clone_optimizer.state[parameter] for parameter in clone.parameters())
    checkpoint.verify_resume(
        loaded,
        lineage=lineage,
        trainable_inventory=loaded.trainable_inventory,
        pass_limit=3,
    )
    with pytest.raises(ValueError, match="lineage"):
        checkpoint.verify_resume(
            loaded,
            lineage=checkpoint.StateLineage("9" * 64, "2" * 64, "3" * 64, "4" * 64),
            trainable_inventory=loaded.trainable_inventory,
            pass_limit=3,
        )
    with pytest.raises(ValueError, match="no_remaining"):
        checkpoint.verify_resume(
            loaded,
            lineage=lineage,
            trainable_inventory=loaded.trainable_inventory,
            pass_limit=1,
        )


def test_selected_snapshot_is_model_only_and_points_at_its_source_state(tmp_path):
    model = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
    model(torch.ones(1, 2)).sum().backward()
    optimizer.step()
    lineage = checkpoint.StateLineage("1" * 64, "2" * 64, "3" * 64, "4" * 64)
    full = checkpoint.save_state(
        tmp_path / "latest",
        model_named=list(model.named_parameters()),
        optimizer=optimizer,
        torch=torch,
        python_random=random.Random(5),
        lineage=lineage,
        pass_index=1,
        optimizer_step=1,
    )
    selected = checkpoint.save_selected(
        tmp_path / "best",
        model_named=list(model.named_parameters()),
        lineage=lineage,
        pass_index=1,
        source_state_sha256=full.tensors_sha256,
    )
    assert selected.source_state_sha256 == full.tensors_sha256
    assert set(selected.trainable_inventory) == {"weight", "bias"}
    weights, loaded = checkpoint.load_selected(tmp_path / "best")
    assert set(weights) == {"model.weight", "model.bias"}
    assert loaded.pass_index == 1
    assert (tmp_path / "best" / "state.safetensors").stat().st_size < (
        tmp_path / "latest" / "state.safetensors"
    ).stat().st_size


def test_checkpoint_rejects_tampered_weights(tmp_path):
    model = torch.nn.Linear(2, 2)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
    model(torch.ones(1, 2)).sum().backward()
    optimizer.step()
    lineage = checkpoint.StateLineage("1" * 64, "2" * 64, "3" * 64, "4" * 64)
    checkpoint.save_state(
        tmp_path / "state",
        model_named=list(model.named_parameters()),
        optimizer=optimizer,
        torch=torch,
        python_random=random.Random(5),
        lineage=lineage,
        pass_index=1,
        optimizer_step=1,
    )
    weights = tmp_path / "state" / "state.safetensors"
    weights.write_bytes(weights.read_bytes()[:-1] + b"\x00")
    with pytest.raises(ValueError, match="hash_mismatch"):
        checkpoint.load_state(tmp_path / "state")


def test_budget_checks_are_cooperative_and_aggregate():
    assert cpu_limit_to_install(100, None) == 100
    assert cpu_limit_to_install(100, 1000) == 100
    assert cpu_limit_to_install(2000, 1000) == 1000
    now = [0.0]
    budget = RunBudget(
        BudgetPlan(5.0, 5.0, 10.0, 10.0, 100.0, 1000),
        Path("."),
        clock=lambda: now[0],
        rss_mb=lambda: 1.0,
        disk_bytes=lambda path: 0,
        abort=lambda reason, output: None,
    )
    budget.checkpoint()
    now[0] = 11.0
    with pytest.raises(BudgetExceeded, match="aggregate_wall"):
        budget.checkpoint()


def test_phase_wall_is_enforced_without_touching_process_limits(tmp_path):
    now = [0.0]
    budget = RunBudget(
        BudgetPlan(5.0, 5.0, 100.0, 10.0, 100.0, 1000),
        tmp_path,
        clock=lambda: now[0],
        rss_mb=lambda: 1.0,
        disk_bytes=lambda path: 0,
        abort=lambda reason, output: None,
    )
    with pytest.raises(BudgetExceeded, match="phase_wall"):
        with budget.phase("train-1", 5.0):
            now[0] = 6.0
            budget.checkpoint()
    assert budget.phase_seconds["train-1"] >= 5.0
