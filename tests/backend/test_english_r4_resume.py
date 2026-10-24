"""Recovery evidence and shuffled-log repair without models or training."""

import copy
import json

import pytest

from research.english.r4_resume import compatible_lineage, repair_report
from research.pretrained.artifacts import canonical_hash, digest_file


def test_continuation_only_allows_scoring_changes_with_identical_data_and_recipe():
    old = {"recipe": {"seed": 5}, "source_files": {"research/english/r4_runtime.py": "old"}}
    current = {
        "recipe": {"seed": 5},
        "source_files": {
            "research/english/r4_runtime.py": "fixed",
            "research/english/r4_resume.py": "new",
        },
    }
    compatible_lineage(old, current)
    changed_recipe = copy.deepcopy(current)
    changed_recipe["recipe"]["seed"] = 6
    with pytest.raises(ValueError, match="recipe"):
        compatible_lineage(old, changed_recipe)
    changed_training = copy.deepcopy(current)
    changed_training["source_files"]["research/english/r4_run.py"] = "different"
    with pytest.raises(ValueError, match="unrelated"):
        compatible_lineage(old, changed_training)


def test_report_repair_preserves_original_evidence_and_scores_by_sample_id(tmp_path):
    stage = tmp_path / "prior/baseline"
    stage.mkdir(parents=True)
    output = tmp_path / "corrected"
    output.mkdir()
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    config = checkpoint / "config.json"
    config.write_text("{}")
    lineage = {"recipe": {"seed": 5}, "source_files": {}}
    original = {
        "status": "measured_research_only",
        "stage": "baseline",
        "test_evaluated": False,
        "lineage": lineage,
        "lineage_hash": canonical_hash(lineage),
        "candidates": [
            {
                "kind": "trocr_original",
                "name": "trocr_original",
                "checkpoint_path": str(checkpoint),
                "checkpoint_files": {"config.json": digest_file(config)},
                "validation_metrics": {"accuracy": 0, "per_crop_ms_p50": 2, "per_crop_ms_p95": 3},
            }
        ],
    }
    source_report = stage / "report.json"
    source_report.write_text(json.dumps(original))
    original_bytes = source_report.read_bytes()
    records = [
        {"sample_id": "two", "truth": "b", "prediction": "b"},
        {"sample_id": "one", "truth": "A", "prediction": "A"},
    ]
    (stage / "validation-original-trocr-predictions-private.jsonl").write_text(
        "\n".join(json.dumps(row) for row in records) + "\n"
    )
    rows = [
        {"id": "one", "label": "A", "writer": "w1"},
        {"id": "two", "label": "b", "writer": "w2"},
    ]
    result = repair_report(
        tmp_path / "prior",
        "baseline",
        {"selected": {"validation": rows}},
        {"lineage": lineage, "lineage_hash": canonical_hash(lineage)},
        output,
    )
    assert source_report.read_bytes() == original_bytes
    assert result["candidates"][0]["validation_metrics"]["accuracy"] == 1
    assert result["scoring_repair"]["training_repeated"] is False
    assert result["scoring_repair"]["held_out_test_loaded"] is False
