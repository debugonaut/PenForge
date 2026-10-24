"""Strict R5-composition initialization tests with synthetic tensors only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import torch
from safetensors.numpy import load_file, save_file
from test_english_r6_model_fixtures import (
    REPO_ROOT,
    build_case,
    file_inventory,
    trainable_keys,
)

from research.english.r6_model import initialization
from research.pretrained.artifacts import canonical_hash, digest_file


def verify(case, *, winner: str | None = None, delta: str | None = None):
    return initialization.verify_initialization(
        initialization.Initialization(
            base_checkpoint=case["base"],
            delta_checkpoint=case["delta"],
            winner_file=case["winner"],
        ),
        expected=initialization.ExpectedHashes(
            winner_sha256=case["expected_winner_sha256"] if winner is None else winner,
            delta_sha256=case["expected_delta_sha256"] if delta is None else delta,
        ),
    )


def rewrite_delta(case, *, drop=None, extra=None, rescale=None, state_patch=None) -> None:
    """Rewrite the fixture delta consistently, so only the R6 scope check can catch it."""
    delta = case["delta"]
    tensors = load_file(str(delta / "state.safetensors"))
    if drop is not None:
        tensors.pop("model." + drop)
    if extra is not None:
        name, array = extra
        tensors["model." + name] = array
    if rescale is not None:
        name, array = rescale
        tensors["model." + name] = array
    save_file(tensors, str(delta / "state.safetensors"))
    state = json.loads((delta / "state.json").read_text())
    state["trainable_inventory"] = {
        key.removeprefix("model."): {
            "dtype": "float32",
            "shape": [int(size) for size in value.shape],
        }
        for key, value in tensors.items()
    }
    state["tensor_count"] = len(tensors)
    state["tensors_sha256"] = digest_file(delta / "state.safetensors")
    if state_patch:
        state.update(state_patch)
    (delta / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    # The frozen winner pins the delta inventory, so keep it consistent as well.
    winner = json.loads(case["winner"].read_text())
    winner["winner"]["checkpoint_files"] = file_inventory(delta)
    (case["winner"]).write_text(json.dumps(winner, indent=2, sort_keys=True), encoding="utf-8")
    case["expected_delta_sha256"] = digest_file(delta / "state.safetensors")
    case["expected_winner_sha256"] = digest_file(case["winner"])


def test_verified_composition_reports_the_complete_trainable_scope(tmp_path):
    case = build_case(tmp_path / "case")
    metadata = verify(case)
    assert metadata["policy"] == "verified_r4_base_plus_r5_selected_trainable_delta"
    assert metadata["decoder_layers"] == 3
    assert metadata["trainable_tensor_count"] == len(trainable_keys()) == 5
    assert sorted(metadata["trainable_inventory"]) == sorted(trainable_keys())
    assert metadata["effective_token_ids"] == {
        "decoder_start_token_id": 2,
        "eos_token_id": 2,
        "pad_token_id": 1,
    }
    assert metadata["base_inventory_sha256"] == canonical_hash(metadata["base_files"])
    assert metadata["delta_sha256"] == case["expected_delta_sha256"]
    assert metadata["optimizer"] == "new_no_r5_optimizer_in_selected_state"
    assert metadata["base_tensor_count"] == 9


def test_expected_scope_is_derived_from_the_base_checkpoint_not_a_literal(tmp_path):
    case = build_case(tmp_path / "case")
    from research.english.r6_contract import tensor_header

    header = tensor_header(case["base"] / "model.safetensors")
    expected = initialization.expected_trainable_keys(header, 3)
    assert expected == set(trainable_keys())
    # A base with four decoder layers would require layers 2 and 3 instead.
    four_layers = dict(header)
    four_layers["decoder.model.decoder.layers.3.weight"] = header[
        "decoder.model.decoder.layers.2.weight"
    ]
    taller = initialization.expected_trainable_keys(four_layers, 4)
    assert "decoder.model.decoder.layers.3.weight" in taller
    assert "decoder.model.decoder.layers.1.weight" not in taller


def test_delta_missing_a_trainable_tensor_is_rejected(tmp_path):
    case = build_case(tmp_path / "case")
    rewrite_delta(case, drop="decoder.model.decoder.layers.2.bias")
    with pytest.raises(ValueError, match="r6_delta_does_not_cover_exactly"):
        verify(case)


def test_delta_with_an_extra_tensor_is_rejected(tmp_path):
    case = build_case(tmp_path / "case")
    rewrite_delta(
        case,
        extra=(
            "decoder.model.decoder.layers.0.weight",
            np.zeros((6, 6), dtype=np.float32),
        ),
    )
    with pytest.raises(ValueError, match="r6_delta_does_not_cover_exactly"):
        verify(case)


def test_delta_shape_mismatch_is_rejected(tmp_path):
    case = build_case(tmp_path / "case")
    rewrite_delta(
        case,
        rescale=(
            "decoder.model.decoder.layers.1.weight",
            np.zeros((6, 5), dtype=np.float32),
        ),
    )
    with pytest.raises(ValueError, match="r6_delta"):
        verify(case)


def test_wrong_frozen_winner_hash_is_rejected(tmp_path):
    case = build_case(tmp_path / "case")
    with pytest.raises(ValueError):
        verify(case, winner="0" * 64)
    with pytest.raises(ValueError):
        verify(case, delta="0" * 64)


def test_delta_state_must_be_a_weights_only_pass_three_snapshot(tmp_path):
    case = build_case(tmp_path / "case")
    rewrite_delta(case, state_patch={"pass_index": 2})
    with pytest.raises(ValueError):
        verify(case)


def test_base_token_ids_must_match_the_frozen_composition(tmp_path):
    case = build_case(tmp_path / "case")
    config = json.loads((case["base"] / "config.json").read_text())
    config["decoder"]["eos_token_id"] = 5
    (case["base"] / "config.json").write_text(json.dumps(config), encoding="utf-8")
    winner = json.loads(case["winner"].read_text())
    winner["lineage"]["initialization_adapter"]["derived_files"] = file_inventory(case["base"])
    winner["lineage_hash"] = canonical_hash(winner["lineage"])
    case["winner"].write_text(json.dumps(winner, indent=2, sort_keys=True), encoding="utf-8")
    case["expected_winner_sha256"] = digest_file(case["winner"])
    with pytest.raises(ValueError, match="token_ids_changed"):
        verify(case)


def test_base_directory_inventory_must_stay_exact(tmp_path):
    case = build_case(tmp_path / "case")
    (case["base"] / "optimizer.pt").write_bytes(b"unexpected")
    with pytest.raises(ValueError):
        verify(case)


class Tiny(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.encoder = torch.nn.Linear(1, 1, bias=False)
        self.encoder.weight.requires_grad_(False)
        self.first = torch.nn.Linear(2, 2)
        self.second = torch.nn.Linear(2, 2)
        for parameter in (self.first, self.second):
            for item in parameter.parameters():
                item.requires_grad_(True)


def tiny_inventory() -> dict[str, dict]:
    return {
        "first.weight": {"dtype": "float32", "shape": [2, 2]},
        "first.bias": {"dtype": "float32", "shape": [2]},
        "second.weight": {"dtype": "float32", "shape": [2, 2]},
        "second.bias": {"dtype": "float32", "shape": [2]},
    }


def tiny_tensors(value: float = 1.0) -> dict[str, torch.Tensor]:
    return {
        "model.first.weight": torch.full((2, 2), value),
        "model.first.bias": torch.full((2,), value),
        "model.second.weight": torch.full((2, 2), value),
        "model.second.bias": torch.full((2,), value),
    }


def test_apply_delta_copies_exactly_the_trainable_scope():
    model = Tiny()
    result = initialization.apply_delta(torch, model, tiny_tensors(3.0), tiny_inventory())
    assert result["applied_tensors"] == 4
    assert torch.equal(model.first.weight, torch.full((2, 2), 3.0))
    assert torch.equal(model.second.bias, torch.full((2,), 3.0))
    assert torch.equal(model.encoder.weight, torch.full((1, 1), model.encoder.weight[0, 0]))


def test_apply_delta_rejects_missing_extra_and_mismatched_tensors():
    model = Tiny()
    inventory = tiny_inventory()
    missing = tiny_tensors()
    missing.pop("model.second.bias")
    with pytest.raises(ValueError, match="missing_required_trainable_tensors"):
        initialization.apply_delta(torch, model, missing, inventory)
    extra = tiny_tensors()
    extra["model.encoder.weight"] = torch.zeros((1, 1))
    with pytest.raises(ValueError, match="unexpected_tensors"):
        initialization.apply_delta(torch, model, extra, inventory)
    wrong = tiny_tensors()
    wrong["model.first.weight"] = torch.zeros((2, 3))
    with pytest.raises(ValueError, match="shape_differs_from_model"):
        initialization.apply_delta(torch, model, wrong, inventory)
    integer = tiny_tensors()
    integer["model.first.weight"] = torch.zeros((2, 2), dtype=torch.int64)
    with pytest.raises(ValueError, match="dtype_differs_from_model"):
        initialization.apply_delta(torch, model, integer, inventory)


def test_apply_delta_refuses_a_trainable_encoder_or_unknown_scope():
    model = Tiny()
    for parameter in model.encoder.parameters():
        parameter.requires_grad_(True)
    with pytest.raises(ValueError):
        initialization.apply_delta(torch, model, tiny_tensors(), tiny_inventory())
    dropped = tiny_inventory()
    dropped.pop("first.bias")
    with pytest.raises(ValueError, match="trainable_names_differ"):
        initialization.apply_delta(torch, Tiny(), tiny_tensors(), dropped)


def test_import_and_metadata_preflight_load_no_model(tmp_path):
    script = """
import sys
from pathlib import Path
from research.english.r6_model import ExpectedHashes, preflight
from test_english_r6_model_fixtures import build_case, build_plan, fake_verifier

root = Path(sys.argv[1])
case = build_case(root)
plan = build_plan(case, root / "run")
prepared = preflight(
    plan,
    bundle_verifier=fake_verifier,
    expected_initialization=ExpectedHashes(
        winner_sha256=case["expected_winner_sha256"],
        delta_sha256=case["expected_delta_sha256"],
    ),
)
assert prepared.lineage["test_access"] == "not_requested_by_part_b"
assert prepared.recipe.pass_limit == 2
assert prepared.lineage["initialization"]["trainable_tensor_count"] == 5
assert not (root / "run").exists(), "preflight created an output directory"
assert "torch" not in sys.modules, "torch was imported during metadata preflight"
assert "transformers" not in sys.modules, "transformers was imported during metadata preflight"
print("INERT_OK")
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), str(REPO_ROOT / "packages/contracts"), str(Path(__file__).parent)]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "case")],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "INERT_OK" in completed.stdout


def test_package_import_is_side_effect_free():
    script = """
import sys
import research.english.r6_model  # noqa: F401
assert "torch" not in sys.modules
assert "transformers" not in sys.modules
print("IMPORT_OK")
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), str(REPO_ROOT / "packages/contracts")]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "IMPORT_OK" in completed.stdout
