"""Part A strict data/scoring contracts, using inert bytes and synthetic rows only."""

import json
import struct
from pathlib import Path

import pytest

from research.english.r5_data.common import canonical_hash, digest_file
from research.english.r5_eval.lifecycle import file_inventory
from research.english.r6_contract import AggregateLimits, R5Initialization, tensor_header
from research.english.r6_eval.scoring import paired_comparison, score_predictions, validation_scorer


def rows():
    return [
        {"id": "one", "label": "A", "writer": "w1", "split": "validation"},
        {"id": "two", "label": "a", "writer": "w2", "split": "validation"},
        {"id": "three", "label": "I", "writer": "w1", "split": "validation"},
        {"id": "four", "label": "l", "writer": "w2", "split": "validation"},
    ]


def predictions(texts=("A", "a", "I", "l")):
    return [
        {"sample_id": row["id"], "truth": row["label"], "prediction": text}
        for row, text in zip(rows(), texts, strict=True)
    ]


def test_raw_scoring_is_order_independent_and_does_not_clean_predictions():
    records = predictions(("a", "a ", "", "ll"))
    score = score_predictions(rows(), list(reversed(records)), rounds=100)
    assert score["correct"] == 0
    assert score["case_only_errors"] == 1
    assert score["scalar_cer"] == 1
    assert score["by_letter"]["A"]["n"] == 1
    assert score["writer_clustered_confidence_intervals"]["writer_clusters"] == 2
    assert score["writer_macro_accuracy"] == 0


@pytest.mark.parametrize("kind", ["duplicate", "missing", "unknown", "wrong_truth", "not_text"])
def test_strict_join_rejects_corruption(kind):
    records = predictions()
    if kind == "duplicate":
        records[-1] = records[0]
    elif kind == "missing":
        records.pop()
    elif kind == "unknown":
        records[0]["sample_id"] = "foreign"
    elif kind == "wrong_truth":
        records[0]["truth"] = "a"
    else:
        records[0]["prediction"] = None
    with pytest.raises(ValueError):
        score_predictions(rows(), records, rounds=100)


@pytest.mark.parametrize("split", ["train", "test"])
def test_callback_rejects_nonvalidation_rows(split):
    with pytest.raises(ValueError):
        validation_scorer([{**rows()[0], "split": split}])


def test_paired_fixes_regressions_and_uncertainty_are_reproducible():
    old = predictions(("a", "a", "l", "l"))
    new = predictions(("A", "A", "I", "l"))
    value = paired_comparison(rows(), old, new, rounds=100)
    assert value["paired"] == {
        "fixed": 2,
        "regressed": 1,
        "persistent_error": 0,
        "correct_control": 1,
    }
    assert value["accuracy_gain"] == 0.25
    assert paired_comparison(rows(), list(reversed(old)), new, rounds=100) == value


def write_tensor(path, header=None):
    header = header or {"model.w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
    data = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(data)) + data + b"\0" * 4)


def initialization_fixture(tmp_path):
    base, delta = tmp_path / "base", tmp_path / "delta"
    base.mkdir()
    delta.mkdir()
    (base / "config.json").write_text("{}")
    (base / "generation_config.json").write_text("{}")
    (base / "model.safetensors").write_bytes(b"inert base bytes")
    write_tensor(delta / "state.safetensors")
    sha = digest_file(delta / "state.safetensors")
    state = {
        "version": "english-r5-selected-state-v1",
        "status": "pass_complete",
        "weights_only": True,
        "pass_index": 3,
        "tensors_sha256": sha,
        "lineage_hash": "fixture",
        "trainable_inventory": {"w": {"shape": [1], "dtype": "float32"}},
    }
    (delta / "state.json").write_text(json.dumps(state))
    lineage = {
        "model_lineage_hash": "fixture",
        "initialization_adapter": {
            "derived_path": str(base),
            "derived_files": file_inventory(base),
        },
    }
    winner = {
        "status": "frozen_before_final_test",
        "test_evaluated": False,
        "lineage": lineage,
        "lineage_hash": canonical_hash(lineage),
        "winner": {
            "kind": "r5_trainable_delta",
            "checkpoint_path": str(delta),
            "checkpoint_files": file_inventory(delta),
        },
    }
    path = tmp_path / "winner.json"
    path.write_text(json.dumps(winner))
    return R5Initialization(base, delta, path), digest_file(path), sha


def test_initialization_requires_composed_base_and_delta_and_pins_payload(tmp_path):
    init, win, sha = initialization_fixture(tmp_path)
    value = init.verify(expected_winner=win, expected_delta=sha)
    assert value["policy"] == "verified_r4_base_plus_r5_selected_trainable_delta"
    assert len(value["trainable_inventory"]) == 1
    (init.base_checkpoint / "config.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="complete_frozen_r5_composition"):
        init.verify(expected_winner=win, expected_delta=sha)


@pytest.mark.parametrize("mutation", ["winner", "delta", "missing_base", "optimizer_state"])
def test_initialization_drift_and_delta_only_fail(tmp_path, mutation):
    init, win, sha = initialization_fixture(tmp_path)
    if mutation == "winner":
        init.winner_file.write_text("{}")
    elif mutation == "delta":
        (init.delta_checkpoint / "state.safetensors").write_bytes(b"changed")
    elif mutation == "missing_base":
        (init.base_checkpoint / "model.safetensors").unlink()
    else:
        (init.delta_checkpoint / "optimizer.json").write_text("{}")
    with pytest.raises(ValueError):
        init.verify(expected_winner=win, expected_delta=sha)


@pytest.mark.parametrize(
    "header",
    [
        {"model.w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 4]}},
        {"model.w": {"dtype": "F32", "shape": [1], "data_offsets": [1, 5]}},
        {"model.w": {"dtype": "pickle", "shape": [1], "data_offsets": [0, 4]}},
        {"model.w": {"dtype": "F32", "shape": [True], "data_offsets": [0, 4]}},
    ],
)
def test_header_only_parser_rejects_invalid_inventory(tmp_path, header):
    path = tmp_path / "state.safetensors"
    write_tensor(path, header)
    with pytest.raises(ValueError):
        tensor_header(path)


@pytest.mark.parametrize(
    "key,value",
    [
        ("wall_seconds", 16201),
        ("cpu_seconds", 32401),
        ("rss_mb", 6001),
        ("output_bytes", 3000000001),
        ("total_passes", 4),
        ("wall_seconds", True),
    ],
)
def test_aggregate_envelope_cannot_be_raised(key, value):
    with pytest.raises(ValueError):
        AggregateLimits(**{key: value})


def test_audit_rejects_public_output_before_reading_private_images():
    from research.english.r6_data.audit import prepare_audit

    with pytest.raises(ValueError, match="private_audit"):
        prepare_audit(Path("/missing"), Path("/missing"), Path("public/r6-private-audit"))


def test_fresh_availability_is_metadata_upper_bound_and_never_reads_or_decodes_test_images(
    tmp_path,
):
    import zipfile

    from research.english.r6_data.fresh import audit_fresh

    source, stage = tmp_path / "sources", tmp_path / "stage"
    source.mkdir()
    stage.mkdir()
    payload = b"inert non-image, CRC metadata only"
    with zipfile.ZipFile(source / "by_class.zip", "w") as archive:
        archive.writestr("by_class/41/hsf_0/a.png", payload)
    with zipfile.ZipFile(source / "by_write.zip", "w") as archive:
        archive.writestr("by_write/hsf_0/f0000_01/u0000_01/u0000_01_00000.png", payload)
    (stage / "preparation.json").write_text(
        json.dumps(
            {
                "sources": {
                    name: {"sha256": digest_file(source / name)}
                    for name in ("by_class.zip", "by_write.zip")
                }
            }
        )
    )
    (stage / "exposure.json").write_text('{"writers":{}}')
    for name in ("train", "validation", "final-test", "school-test"):
        (stage / (name + ".jsonl")).write_text("")
    result = audit_fresh(stage, source)
    assert result["source_writers"] == 1
    assert result["remaining_writers"] == 1
    assert result["coverage_upper_bound"]["primary"]["A"] == 1
    assert result["proven_below_minimum"] == {"primary": True, "school": True}
    assert result["final_images_viewed"] is False
    assert result["final_status"] == "unavailable_below_minimum"


def test_duplicate_byte_identity_inside_training_split_is_rejected():
    from research.english.r6_contract import TrainingInputs

    def row(identity, split, digest):
        return {
            "id": identity,
            "hash": digest * 64,
            "path": f"images/{digest * 64}.png",
            "pixel_hash": digest * 64,
            "label": "A",
            "writer": identity,
            "source_group": identity,
            "source_partition": 0,
            "source_member": identity,
            "split": split,
        }

    with pytest.raises(ValueError, match="duplicate_training_validation_identity"):
        TrainingInputs(
            (row("one", "train", "a"), row("two", "train", "a")),
            (row("three", "validation", "b"),),
            lambda records: {},
        )


def stage_fixture(root):
    import io

    from PIL import Image

    from research.english.r5_data.common import jsonl
    from research.english.r6_contract import CLASSES

    source = root / "original"
    (source / "images").mkdir(parents=True)
    groups = {"train": [], "validation": []}
    for split_index, split in enumerate(groups):
        for index, label in enumerate(CLASSES):
            image = Image.new("L", (128, 128), 255)
            image.putpixel((index + 2, split_index + 3), 0)
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            data = stream.getvalue()
            import hashlib

            sha = hashlib.sha256(data).hexdigest()
            (source / f"images/{sha}.png").write_bytes(data)
            groups[split].append(
                {
                    "id": f"{split}-{index}",
                    "hash": sha,
                    "path": f"images/{sha}.png",
                    "pixel_hash": hashlib.sha256(image.tobytes()).hexdigest(),
                    "label": label,
                    "writer": split,
                    "source_group": split,
                    "source_partition": 0,
                    "split": split,
                    "source_member": f"private/{split}/{index}.png",
                }
            )
    for split, group in groups.items():
        (source / (split + ".jsonl")).write_bytes(jsonl(group))
    (source / "train-validation.jsonl").write_bytes(jsonl(groups["train"] + groups["validation"]))
    exposure = {
        "writers": {"train": "train", "validation": "validation"},
        "sources": [],
        "admissions": [],
    }
    (source / "exposure.json").write_text(json.dumps(exposure))
    prep = {
        "files": {
            name: digest_file(source / name)
            for name in (
                "train.jsonl",
                "validation.jsonl",
                "train-validation.jsonl",
                "exposure.json",
            )
        },
        "source_code_sha256": {},
        "sources": {},
    }
    (source / "preparation.json").write_text(json.dumps(prep))
    (source / "admission.json").write_text(
        json.dumps(
            {
                "scope": "local_noncommercial_isolated_letters_research",
                "preparation_sha256": digest_file(source / "preparation.json"),
            }
        )
    )
    return source


def test_exact_data_view_has_no_test_rows_and_refuses_inventory_or_byte_drift(tmp_path):
    from research.english.r6_data.prepare import check, prepare

    source = stage_fixture(tmp_path)
    original = {p.name: digest_file(p) for p in source.iterdir() if p.is_file()}
    destination = tmp_path / "new"
    result = prepare(source, destination)
    assert result["status"] == "r6_data_verified_only"
    assert result["receipt"]["test_rows"] == 0
    assert set(p.name for p in destination.iterdir()) == {
        "images",
        "train.jsonl",
        "validation.jsonl",
        "train-validation.jsonl",
        "r6-stage.json",
    }
    assert {p.name: digest_file(p) for p in source.iterdir() if p.is_file()} == original
    (destination / "final-test.jsonl").write_text('{"split":"test"}\n')
    with pytest.raises(ValueError, match="unexpected_or_test_files"):
        check(destination)
    (destination / "final-test.jsonl").unlink()
    image = next((destination / "images").iterdir())
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="image_content_changed"):
        check(destination)
    with pytest.raises(ValueError, match="new_separate"):
        prepare(source, destination)
