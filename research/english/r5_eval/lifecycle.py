"""Data-only winner freezing and persistent one-time final-test claims."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from research.english.r5_data.common import canonical_hash, digest_file, write_json


def file_inventory(folder: Path) -> dict[str, str]:
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("r5_checkpoint_directory_missing_or_unsafe")
    result = {}
    for item in sorted(folder.iterdir()):
        if item.is_symlink() or not item.is_file():
            raise ValueError("r5_checkpoint_requires_flat_regular_file_inventory")
        result[item.name] = digest_file(item)
    if not result:
        raise ValueError("r5_checkpoint_inventory_empty")
    return result


def rank_key(candidate: dict) -> tuple:
    metrics = candidate["validation_metrics"]
    n, correct = metrics.get("n"), metrics.get("correct")
    accuracy, cer, latency = (
        metrics.get("accuracy"),
        metrics.get("scalar_cer"),
        candidate.get("per_crop_ms_p50"),
    )
    if (
        metrics.get("exact_case_sensitive") is not True
        or type(n) is not int
        or n < 1
        or type(correct) is not int
        or not 0 <= correct <= n
    ):
        raise ValueError("r5_selection_requires_exact_case_counts")
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
        for value in (accuracy, cer, latency)
    ):
        raise ValueError("r5_selection_requires_finite_measured_metrics")
    accuracy, cer, latency = (
        float(metrics["accuracy"]),
        float(metrics["scalar_cer"]),
        float(candidate["per_crop_ms_p50"]),
    )
    if abs(accuracy - correct / n) > 1e-12 or cer < 0 or latency < 0:
        raise ValueError("r5_selection_metric_count_or_range_mismatch")
    return -accuracy, cer, latency, candidate["name"]


def freeze(candidates: list[dict], *, lineage: dict, evidence: list[Path], output: Path) -> dict:
    if not candidates or len({c["name"] for c in candidates}) != len(candidates):
        raise ValueError("r5_selection_candidates_empty_or_duplicate")
    if len({c["validation_metrics"]["n"] for c in candidates}) != 1:
        raise ValueError("r5_candidates_require_identical_validation_inventory")
    ranked = sorted(candidates, key=rank_key)
    # All candidates passed by the caller are retained checkpoints, not unretained training passes.
    for candidate in ranked:
        if file_inventory(Path(candidate["checkpoint_path"])) != candidate["checkpoint_files"]:
            raise ValueError("r5_candidate_checkpoint_changed_before_freeze")
    for path in evidence:
        if path.is_symlink() or not path.is_file():
            raise ValueError("r5_selection_evidence_missing_or_unsafe")
    result = {
        "version": "english-r5-frozen-winner-v1",
        "status": "frozen_before_final_test",
        "lineage": lineage,
        "lineage_hash": canonical_hash(lineage),
        "rule": "validation_exact_accuracy_then_scalar_cer_then_measured_latency_then_name",
        "winner": ranked[0],
        "ranked_candidates": ranked,
        "source_reports": [
            {"path": str(path.resolve()), "sha256": digest_file(path)} for path in evidence
        ],
        "test_evaluated": False,
        "product_qualification": "none",
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(output, result)
    return result


def verify_frozen(path: Path, lineage: dict) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("r5_final_test_requires_frozen_winner")
    value = json.loads(path.read_text())
    if (
        value.get("version") != "english-r5-frozen-winner-v1"
        or value.get("status") != "frozen_before_final_test"
        or value.get("lineage") != lineage
        or value.get("lineage_hash") != canonical_hash(lineage)
        or value.get("test_evaluated") is not False
    ):
        raise ValueError("r5_frozen_winner_lineage_or_status_changed")
    candidates = value["ranked_candidates"]
    if value["winner"] != sorted(candidates, key=rank_key)[0] or candidates != sorted(
        candidates, key=rank_key
    ):
        raise ValueError("r5_frozen_winner_selection_changed")
    for report in value["source_reports"]:
        target = Path(report["path"])
        if target.is_symlink() or not target.is_file() or digest_file(target) != report["sha256"]:
            raise ValueError("r5_selection_evidence_changed_after_freeze")
    winner = value["winner"]
    if file_inventory(Path(winner["checkpoint_path"])) != winner["checkpoint_files"]:
        raise ValueError("r5_winner_checkpoint_changed_after_freeze")
    return value


def claim_final_test(rows: list[dict], frozen_path: Path, ledger: Path) -> dict:
    """One persistent claim per frozen final inventory, shared across output directories."""
    if not rows or any(row["split"] != "test" for row in rows):
        raise ValueError("r5_final_claim_requires_nonempty_test_rows")
    fingerprint = canonical_hash(
        sorted((row["id"], row["hash"], row["writer"], row["label"]) for row in rows)
    )
    if ledger.is_symlink():
        raise ValueError("r5_final_claim_ledger_must_not_be_symlink")
    ledger.mkdir(parents=True, exist_ok=True, mode=0o700)
    result = {
        "version": "r5-one-time-final-claim-v1",
        "status": "claimed_before_model_load",
        "final_inventory_hash": fingerprint,
        "frozen_winner_sha256": digest_file(frozen_path),
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "retry_policy": "no_automatic_retry_even_after_interruption",
    }
    try:
        write_json(ledger / (fingerprint + ".json"), result)
    except FileExistsError:
        raise ValueError("r5_final_inventory_already_claimed_no_automatic_rerun") from None
    return result
