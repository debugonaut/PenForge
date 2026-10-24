"""R6 validation freezing. A fresh final inventory is unavailable, so no claims exist."""

from __future__ import annotations

import json
from pathlib import Path

from research.english.r5_data.common import canonical_hash, checked, digest_file, write_json
from research.english.r5_eval.lifecycle import file_inventory
from research.english.r6_contract import SELECTION_RULE, rank_key


def freeze(candidates, *, lineage, evidence, output: Path):
    if output.exists() or output.is_symlink():
        raise ValueError("r6_winner_output_collision")
    if not candidates or len({c["name"] for c in candidates}) != len(candidates):
        raise ValueError("r6_unique_complete_candidates_required")
    if len({c["validation_metrics"]["n"] for c in candidates}) != 1:
        raise ValueError("r6_validation_inventory_mismatch")
    ranked = sorted(candidates, key=rank_key)
    for candidate in ranked:
        if file_inventory(Path(candidate["checkpoint_path"])) != candidate["checkpoint_files"]:
            raise ValueError("r6_retained_checkpoint_changed")
        predictions = Path(candidate["predictions_path"])
        if predictions.is_symlink() or digest_file(predictions) != candidate["predictions_sha256"]:
            raise ValueError("r6_retained_predictions_changed")
    for path in evidence:
        checked(path.parent, path.name)
    value = {
        "version": "english-r6-frozen-winner-v1",
        "status": "frozen_validation_only_final_unavailable",
        "rule": SELECTION_RULE,
        "winner": ranked[0],
        "ranked_candidates": ranked,
        "lineage": lineage,
        "lineage_hash": canonical_hash(lineage),
        "source_reports": [
            {"path": str(path.resolve()), "sha256": digest_file(path)} for path in evidence
        ],
        "test_evaluated": False,
        "fresh_final": "unavailable_below_minimum_untouched_writer_coverage",
        "product_qualification": "none",
        "model_execution": False,
    }
    write_json(output, value)
    return value


def verify_frozen(path: Path, lineage):
    value = json.loads(checked(path.parent, path.name).read_text())
    if (
        value.get("version") != "english-r6-frozen-winner-v1"
        or value.get("lineage") != lineage
        or value.get("lineage_hash") != canonical_hash(lineage)
    ):
        raise ValueError("r6_frozen_lineage_changed")
    if value.get("winner") != min(value["ranked_candidates"], key=rank_key):
        raise ValueError("r6_frozen_selection_changed")
    for record in value["source_reports"]:
        source = Path(record["path"])
        if digest_file(checked(source.parent, source.name)) != record["sha256"]:
            raise ValueError("r6_frozen_evidence_changed")
    for candidate in value["ranked_candidates"]:
        if file_inventory(Path(candidate["checkpoint_path"])) != candidate["checkpoint_files"]:
            raise ValueError("r6_frozen_checkpoint_changed")
    return value
