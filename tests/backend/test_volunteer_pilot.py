"""Private five-writer volunteer pilot: data-only guarantees.

These tests never import Torch or load a model. Inference behaviour is exercised with
fake tensors, a fake tokenizer and a fake model so the raw-decoding and
generation-limit rules are checked without executing the frozen R6 checkpoint.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from research.english.volunteer import evaluate as evaluate_module
from research.english.volunteer.manifest import (
    COORDINATES_VERSION,
    CROP_GENERATION_VERSION,
    EXPECTED_SAMPLES,
    LOWER,
    MANIFEST_VERSION,
    UPPER,
    WRITERS,
    expected_sample_id,
    load_manifest,
    validate_coordinates,
)
from research.english.volunteer.scoring import score

ROOT = Path(__file__).resolve().parents[2]
OTHER_SHA = "b" * 64


def _coordinates_payload() -> dict[str, Any]:
    writers = []
    for index, writer in enumerate(WRITERS):
        boxes = [
            {
                "label": label,
                "group": "upper",
                "x0": 10 + i * 12,
                "y0": 10,
                "x1": 20 + i * 12,
                "y1": 30,
            }
            for i, label in enumerate(UPPER)
        ]
        boxes += [
            {
                "label": label,
                "group": "lower",
                "x0": 10 + i * 12,
                "y0": 40,
                "x1": 20 + i * 12,
                "y1": 60,
            }
            for i, label in enumerate(LOWER)
        ]
        writers.append(
            {
                "writer_id": writer,
                "source_path": f"/private/{writer}.jpeg",
                "source_sha256": f"{index + 1:064x}",
                "source_bytes": 1000 + index,
                "page_width": 800,
                "page_height": 1200,
                "boxes": boxes,
            }
        )
    return {"version": COORDINATES_VERSION, "writers": writers}


def _manifest_payload() -> dict[str, Any]:
    samples = []
    for writer_index, writer in enumerate(WRITERS):
        digest = f"{writer_index + 1:02d}{'c' * 62}"
        for group, letters in (("upper", UPPER), ("lower", LOWER)):
            for label in letters:
                samples.append(
                    {
                        "sample_id": expected_sample_id(writer, group, label),
                        "writer_id": writer,
                        "group": group,
                        "truth": label,
                        "letter_box": [10, 10, 20, 30],
                        "crop_box": [8, 8, 22, 32],
                        "crop_width": 384,
                        "crop_height": 384,
                        "crop_sha256": digest,
                        "crop_path": f"crops/images/{digest}.png",
                        "source_sha256": f"{writer_index + 1:064x}",
                        "crop_generation_version": CROP_GENERATION_VERSION,
                        "review_status": "reviewed_agent",
                    }
                )
    sources = [
        {
            "writer_id": writer,
            "source_path": f"/private/{writer}.jpeg",
            "source_sha256": f"{index + 1:064x}",
            "source_bytes": 1000 + index,
            "copied_path": f"sources/{writer}.jpeg",
            "page_width": 800,
            "page_height": 1200,
        }
        for index, writer in enumerate(WRITERS)
    ]
    return {
        "version": MANIFEST_VERSION,
        "sources": sources,
        "included_samples": EXPECTED_SAMPLES,
        "excluded": [],
        "samples": samples,
        "crop_generation": {
            "version": CROP_GENERATION_VERSION,
            "margin_fraction": 0.04,
            "square_padding": "centered_white_rgb",
            "output_side": 384,
            "resampling": "lanczos",
            "contrast_adjustment": "none",
            "line_erasure": "none",
        },
    }


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


def test_coordinates_require_reading_order_and_case() -> None:
    writers = validate_coordinates(_coordinates_payload())
    assert [writer.source.writer_id for writer in writers] == list(WRITERS)
    assert writers[0].boxes[0].label == "A"
    assert writers[0].boxes[26].label == "a"

    broken = _coordinates_payload()
    broken["writers"][0]["boxes"][0]["label"] = "a"
    with pytest.raises(ValueError, match="label_case_mismatch"):
        validate_coordinates(broken)

    swapped = _coordinates_payload()
    first, second = swapped["writers"][0]["boxes"][1:3]
    first["label"], second["label"] = "C", "B"
    with pytest.raises(ValueError, match="label_order_changed"):
        validate_coordinates(swapped)


def test_coordinates_reject_shared_sources_and_off_page_boxes() -> None:
    shared = _coordinates_payload()
    shared["writers"][1]["source_sha256"] = shared["writers"][0]["source_sha256"]
    with pytest.raises(ValueError, match="distinct_pages"):
        validate_coordinates(shared)

    off_page = _coordinates_payload()
    off_page["writers"][0]["boxes"][0]["x1"] = 5000
    with pytest.raises(ValueError, match="outside_page"):
        validate_coordinates(off_page)


def test_manifest_requires_the_complete_inventory(tmp_path: Path) -> None:
    path = tmp_path / "crop-manifest.json"
    path.write_text(json.dumps(_manifest_payload()))
    manifest = load_manifest(path)
    assert len(manifest.samples) == EXPECTED_SAMPLES
    assert [sample.sample_id for sample in manifest.samples[:2]] == [
        "W01-upper-A",
        "W01-upper-B",
    ]

    payload = _manifest_payload()
    payload["samples"].pop()
    payload["included_samples"] = len(payload["samples"])
    payload["excluded"] = [{"writer_id": "W05", "truth": "Z", "reason": "missing"}]
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="sample_count_changed"):
        load_manifest(path)


def test_manifest_rejects_duplicate_ids_and_unaccounted_exclusions(tmp_path: Path) -> None:
    path = tmp_path / "crop-manifest.json"

    duplicated = _manifest_payload()
    duplicated["samples"][1]["sample_id"] = duplicated["samples"][0]["sample_id"]
    path.write_text(json.dumps(duplicated))
    with pytest.raises(ValueError, match="sample_id_mismatch"):
        load_manifest(path)

    unaccounted = _manifest_payload()
    unaccounted["excluded"] = [{"writer_id": "W01", "truth": "A", "reason": "missing"}]
    path.write_text(json.dumps(unaccounted))
    with pytest.raises(ValueError, match="wrong_inventory"):
        load_manifest(path)


def test_manifest_rejects_writer_source_mismatch(tmp_path: Path) -> None:
    payload = _manifest_payload()
    payload["samples"][0]["source_sha256"] = OTHER_SHA
    path = tmp_path / "crop-manifest.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="multiple_sources"):
        load_manifest(path)


def test_manifest_rejects_a_foreign_crop_path(tmp_path: Path) -> None:
    payload = _manifest_payload()
    payload["samples"][0]["crop_path"] = "crops/images/elsewhere.png"
    path = tmp_path / "crop-manifest.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="not_content_addressed"):
        load_manifest(path)


def _records(correct: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for writer in WRITERS:
        for group, letters in (("upper", UPPER), ("lower", LOWER)):
            for label in letters:
                matched = len(records) < correct
                records.append(
                    {
                        "sample_id": expected_sample_id(writer, group, label),
                        "writer_id": writer,
                        "truth": label,
                        "prediction": label if matched else label.swapcase(),
                    }
                )
    return records


def test_scoring_is_raw_exact_and_case_sensitive() -> None:
    report = score(_records(200))
    assert report["total"] == EXPECTED_SAMPLES
    assert report["correct"] == 200
    assert report["accuracy"] == pytest.approx(200 / EXPECTED_SAMPLES)
    assert report["case_only_errors"] == EXPECTED_SAMPLES - 200
    assert report["casefolded_shape_accuracy"] == pytest.approx(1.0)
    assert report["empty_predictions"] == 0
    assert report["uncertainty"].startswith("not_reported")
    assert set(report["by_writer"]) == set(WRITERS)
    assert set(report["by_case"]) == {"uppercase", "lowercase"}
    assert sum(item["n"] for item in report["confusion"]) == EXPECTED_SAMPLES
    assert report["confusion"][0]["prediction"]


def test_scoring_counts_empty_predictions_and_rejects_bad_records() -> None:
    records = _records(0)
    records[0]["prediction"] = ""
    assert score(records)["empty_predictions"] == 1

    wrong_truth = _records(0)
    wrong_truth[0]["truth"] = "Z"
    with pytest.raises(ValueError, match="truth_mismatch"):
        score(wrong_truth)

    with pytest.raises(ValueError, match="count_differs"):
        score(_records(0)[:-1])


def test_generate_records_keeps_raw_text_and_counts_limit_hits(monkeypatch) -> None:
    class Tensor:
        def __init__(self, rows: list[list[int]]) -> None:
            self.rows = rows

        def tolist(self) -> list[list[int]]:
            return self.rows

    class Torch:
        @staticmethod
        def cat(values: list[Any], dim: int = 0) -> list[Any]:
            return values

        class inference_mode:
            def __enter__(self) -> None:
                return None

            def __exit__(self, *exc: object) -> None:
                return None

    class Tokenizer:
        eos_token_id = 2

        @staticmethod
        def decode(ids: list[int], **kwargs: Any) -> str:
            return "".join(chr(value) for value in ids if value != 2)

    class Model:
        def eval(self) -> Model:
            return self

        def generate(self, **kwargs: Any) -> Tensor:
            return Tensor([[65], [97], [66, 2]])

    class Sample:
        def __init__(self, identity: str) -> None:
            self.sample_id = identity
            self.writer_id = "W01"
            self.truth = identity[-1]

    monkeypatch.setattr(evaluate_module, "crop_pixels", lambda processor, root, sample: [])
    samples = [Sample("W01-upper-A"), Sample("W01-lower-a"), Sample("W01-upper-B")]
    records, stats = evaluate_module.generate_records(
        Torch,
        Model(),
        Tokenizer(),
        object(),
        cast(Any, samples),
        Path("/nonexistent"),
        batch_size=8,
        max_new_tokens=8,
    )
    assert [record["prediction"] for record in records] == ["A", "a", "B"]
    assert [record["truth"] for record in records] == ["A", "a", "B"]
    assert stats["generation_limit_hits"] == 2
    assert stats["batch_size"] == 8
    assert stats["max_new_tokens"] == 8


def test_evaluate_refuses_a_completed_run_before_importing_torch(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "predictions-private.jsonl").write_text("")
    with pytest.raises(FileExistsError, match="already_evaluated"):
        evaluate_module.verify_prepared(run)


def test_prepare_refuses_an_evaluated_run(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "report.json").write_text("{}")
    result = _python(
        "from pathlib import Path\n"
        "from research.english.volunteer.prepare import prepare\n"
        f"try:\n    prepare(Path({str(run)!r}))\n"
        "except FileExistsError:\n    print('refused')\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "refused"


def test_prepare_blocks_model_framework_imports() -> None:
    result = _python(
        "from research.english.volunteer.prepare import model_frameworks_blocked\n"
        "try:\n"
        "    with model_frameworks_blocked():\n"
        "        import torch\n"
        "except ValueError as error:\n"
        "    print(error)\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "volunteer_prepare_must_not_import_model_frameworks"


def test_prepare_imports_no_model_framework() -> None:
    result = _python(
        "import sys\n"
        "import research.english.volunteer.prepare\n"
        "print(','.join(sorted(n for n in ('torch', 'transformers', 'safetensors')"
        " if n in sys.modules)))"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_safetensors_dtype_names_are_normalized() -> None:
    """The frozen inventory records torch names; safetensors headers spell `F32`."""
    from research.english.volunteer.frozen import _normalized_dtype

    assert _normalized_dtype("F32") == "float32"
    assert _normalized_dtype("BF16") == "bfloat16"
    assert _normalized_dtype("float32") == "float32"
    with pytest.raises(ValueError, match="dtype_must_be_text"):
        _normalized_dtype(32)


FROZEN_WINNER = ROOT / "research/runs/r6-owner-20261022-ready/winner-frozen.json"


@pytest.mark.skipif(not FROZEN_WINNER.is_file(), reason="frozen R6 winner is not in this checkout")
def test_frozen_winner_refuses_a_wrong_checkpoint_hash() -> None:
    from research.english.volunteer.frozen import verify_frozen_winner

    with pytest.raises(ValueError, match="checkpoint_hash_changed"):
        verify_frozen_winner(checkpoint_sha256="0" * 64)
    with pytest.raises(ValueError, match="document_hash_changed"):
        verify_frozen_winner(winner_document_sha256="0" * 64)
