"""Read-only independent R5 closeout. Rescores saved predictions, never inference."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from research.english.r5_data.common import canonical_hash, checked, digest_file, read_jsonl
from research.english.r5_eval.lifecycle import verify_frozen
from research.english.r6_contract import R5_DELTA_SHA256, R5_WINNER_SHA256
from research.english.r6_eval.scoring import score_predictions


def verify_r5(run: Path, staging: Path) -> dict:
    run, staging = run.resolve(), staging.resolve()
    session = json.loads(checked(run, "session.json").read_text())
    frozen_path = checked(run, "winner-frozen.json")
    if digest_file(frozen_path) != R5_WINNER_SHA256:
        raise ValueError("r6_r5_closeout_winner_changed")
    frozen = verify_frozen(frozen_path, session)
    delta = Path(frozen["winner"]["checkpoint_path"]) / "state.safetensors"
    if digest_file(delta) != R5_DELTA_SHA256:
        raise ValueError("r6_r5_closeout_delta_changed")
    preparation = json.loads(checked(staging, "preparation.json").read_text())
    if digest_file(staging / "preparation.json") != session["data_preparation_sha256"]:
        raise ValueError("r6_r5_closeout_data_preparation_changed")
    rows_path = checked(staging, "final-test.jsonl")
    if digest_file(rows_path) != preparation["files"]["final-test.jsonl"]:
        raise ValueError("r6_r5_final_mapping_changed")
    rows = read_jsonl(rows_path)
    final = run / "final-test-recovery-1/final-test"
    report_path = checked(final, "report.json")
    report = json.loads(report_path.read_text())
    pred_path = checked(final, "primary-predictions-private.jsonl")
    score = score_predictions(rows, read_jsonl(pred_path))
    if (
        report["status"] != "measured_research_only"
        or report["test_evaluated"] is not True
        or report["lineage_hash"] != canonical_hash(session)
        or report["frozen_winner_sha256"] != R5_WINNER_SHA256
        or digest_file(pred_path) != report["groups"]["primary"]["predictions_sha256"]
        or score != report["groups"]["primary"]["metrics"]
        or len(rows) != 1664
        or len({r["writer"] for r in rows}) != 418
    ):
        raise ValueError("r6_r5_final_report_predictions_or_metrics_mismatch")
    receipt = json.loads(checked(run, "final-test-recovery-1/recovery-manifest.json").read_text())
    source_manifest = checked(run, "execution-source.json")
    source_archive = checked(run, "execution-source.zip")
    source_metadata = json.loads(source_manifest.read_text())
    if (
        digest_file(source_manifest) != session["execution_source_manifest_sha256"]
        or digest_file(source_manifest) != receipt["source_manifest_sha256"]
        or digest_file(source_archive) != source_metadata["archive_sha256"]
        or digest_file(source_archive) != receipt["source_zip_sha256"]
        or digest_file(checked(run, "session.json")) != receipt["original_session_sha256"]
        or digest_file(checked(run, "final-test-recovery-1/winner-frozen.json")) != R5_WINNER_SHA256
    ):
        raise ValueError("r6_r5_frozen_source_or_recovery_manifest_changed")
    with zipfile.ZipFile(source_archive) as archive:
        if set(archive.namelist()) != set(source_metadata["files"]):
            raise ValueError("r6_r5_source_archive_inventory_changed")
        for name, pin in source_metadata["files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != pin:
                raise ValueError("r6_r5_source_archive_member_changed")
    ledger = Path(session["final_claim_ledger"])
    claim = report["claim"]
    fingerprint = canonical_hash(
        sorted((r["id"], r["hash"], r["writer"], r["label"]) for r in rows)
    )
    original = checked(ledger, fingerprint + ".json")
    retry = checked(ledger, fingerprint + ".manual-retry-1.json")
    if (
        json.loads(retry.read_text()) != claim
        or json.loads(checked(final, "started.json").read_text()) != claim
        or digest_file(original) != claim["original_claim_sha256"]
        or digest_file(run / "final-test-recovery-1/recovery-manifest.json")
        != claim["recovery_manifest_sha256"]
        or digest_file(run / "final-test-recovery-1/recovery-runner.py") != claim["helper_sha256"]
        or claim["final_inventory_hash"] != fingerprint
        or json.loads(original.read_text())
        != json.loads(checked(run, "final-test/started.json").read_text())
    ):
        raise ValueError("r6_r5_recovery_claim_lineage_changed")
    for name, pin in receipt["original_attempt_files"].items():
        if digest_file(checked(run / "final-test", name)) != pin:
            raise ValueError("r6_r5_interruption_chronology_changed")
    return {
        "status": "r5_completed_final_independently_verified",
        "model_execution": False,
        "source_report_sha256": digest_file(report_path),
        "predictions_sha256": digest_file(pred_path),
        "final_mapping_sha256": digest_file(rows_path),
        "winner_sha256": R5_WINNER_SHA256,
        "delta_sha256": R5_DELTA_SHA256,
        "samples": len(rows),
        "writers": len({r["writer"] for r in rows}),
        "metrics": score,
        "errors": score["n"] - score["correct"],
        "other_errors": score["n"] - score["correct"] - score["case_only_errors"],
        "original_and_recovery_claims_preserved": True,
        "images_inspected": False,
        "scope": "historical_isolated_letter_final_evidence_not_r6_tuning",
    }
