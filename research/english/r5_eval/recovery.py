"""Explicit owner-run recovery of one interrupted R5 final evaluation.

The original source snapshot, winner, claim and interrupted output remain immutable.
The new helper is pinned separately and runs against the original frozen imports.
Only completed-unit telemetry wraps inference; model inputs and outputs pass through.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from research.english.r5_data.common import canonical_hash, checked, digest_file, write_json
from research.english.r5_eval.lifecycle import verify_frozen

RETRY_DIRECTORY = "final-test-recovery-1"
VERSION = "english-r5-explicit-final-recovery-v1"
AUTHORIZATION = "Owner requested visible progress and explicitly approved preparing one manual retry on 2026-10-22."


def progress(stage: str, **counts):
    print(json.dumps({"stage": stage, **counts}, sort_keys=True), flush=True)


class ProgressModel:
    """Forward the frozen model unchanged; report completed generation batches."""

    def __init__(self, model, total: int):
        self.model = model
        self.total = total
        self.completed = 0
        self.started = time.monotonic()
        self._emit()

    def __getattr__(self, name):
        return getattr(self.model, name)

    def _emit(self):
        progress(
            "r5-final-evaluation",
            completed_samples=self.completed,
            total_samples=self.total,
            elapsed_seconds=round(time.monotonic() - self.started, 1),
        )

    def generate(self, **kwargs):
        result = self.model.generate(**kwargs)
        count = len(result)
        if not 0 < count <= self.total - self.completed:
            raise ValueError("r5_recovery_generation_count_changed")
        previous = self.completed
        self.completed += count
        if self.completed // 64 != previous // 64 or self.completed == self.total:
            self._emit()
        return result


def _evidence(ctx) -> dict:
    run = ctx["run"]
    frozen_path = checked(run, "winner-frozen.json")
    frozen = verify_frozen(frozen_path, ctx["lineage"])
    folder = run / "final-test"
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError("r5_recovery_requires_preserved_interrupted_attempt")
    if {item.name for item in folder.iterdir()} != {"started.json", "failure.json"}:
        raise ValueError("r5_recovery_refuses_completed_or_prediction_bearing_attempt")
    started_path, failure_path = checked(folder, "started.json"), checked(folder, "failure.json")
    started = json.loads(started_path.read_text())
    failure = json.loads(failure_path.read_text())
    if failure != {
        "automatic_retry": False,
        "exception": "KeyboardInterrupt",
        "status": "incomplete_claim_retained",
    }:
        raise ValueError("r5_recovery_requires_owner_interruption_without_results")
    rows = [row for key in ("test", "school_test") for row in ctx["study"]["selected"].get(key, [])]
    if not rows or any(row["split"] != "test" for row in rows):
        raise ValueError("r5_recovery_requires_original_final_inventory")
    fingerprint = canonical_hash(
        sorted((row["id"], row["hash"], row["writer"], row["label"]) for row in rows)
    )
    ledger = Path(ctx["lineage"]["final_claim_ledger"])
    if ledger.is_symlink():
        raise ValueError("r5_recovery_ledger_must_not_be_symlink")
    original_claim = checked(ledger, fingerprint + ".json")
    if (
        json.loads(original_claim.read_text()) != started
        or started.get("status") != "claimed_before_model_load"
        or started.get("final_inventory_hash") != fingerprint
        or started.get("frozen_winner_sha256") != digest_file(frozen_path)
    ):
        raise ValueError("r5_recovery_original_claim_or_winner_changed")
    return {
        "version": VERSION,
        "original_run": str(run.resolve()),
        "original_session_sha256": digest_file(checked(run, "session.json")),
        "original_winner_sha256": digest_file(frozen_path),
        "original_attempt_files": {
            name: digest_file(checked(folder, name)) for name in ("started.json", "failure.json")
        },
        "original_claim_path": str(original_claim.resolve()),
        "original_claim_sha256": digest_file(original_claim),
        "final_inventory_hash": fingerprint,
        "source_manifest_sha256": digest_file(checked(run, "execution-source.json")),
        "source_zip_sha256": digest_file(checked(run, "execution-source.zip")),
        "winner": frozen["winner"]["name"],
        "model_policy": "original_frozen_evaluator_weights_inputs_decoding_and_scoring_unchanged",
        "retry_policy": "one_explicit_owner_invocation_no_automatic_retry",
        "authorization": AUTHORIZATION,
    }


def prepare(ctx, *, helper: Path | None = None) -> dict:
    """Metadata only. Pin the recovery helper without changing any old evidence."""
    helper = Path(__file__) if helper is None else helper
    if helper.is_symlink() or not helper.is_file():
        raise ValueError("r5_recovery_helper_requires_regular_file")
    evidence = _evidence(ctx)
    retry = ctx["run"] / RETRY_DIRECTORY
    if retry.exists() or retry.is_symlink():
        return verify_prepared(ctx)
    retry.mkdir(mode=0o700)
    shutil.copyfile(helper, retry / "recovery-runner.py")
    shutil.copyfile(ctx["run"] / "winner-frozen.json", retry / "winner-frozen.json")
    for name in ("recovery-runner.py", "winner-frozen.json"):
        (retry / name).chmod(0o600)
    receipt = {
        **evidence,
        "status": "prepared_without_model_execution",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "helper_sha256": digest_file(retry / "recovery-runner.py"),
    }
    write_json(retry / "recovery-manifest.json", receipt)
    return verify_prepared(ctx)


def verify_prepared(ctx) -> dict:
    retry = ctx["run"] / RETRY_DIRECTORY
    if retry.is_symlink() or not retry.is_dir():
        raise ValueError("r5_recovery_prepare_required")
    receipt = json.loads(checked(retry, "recovery-manifest.json").read_text())
    expected = _evidence(ctx)
    if (
        set(receipt) != {*expected, "status", "prepared_at", "helper_sha256"}
        or any(receipt.get(key) != value for key, value in expected.items())
        or receipt.get("status") != "prepared_without_model_execution"
        or digest_file(checked(retry, "recovery-runner.py")) != receipt.get("helper_sha256")
        or digest_file(checked(retry, "winner-frozen.json")) != receipt["original_winner_sha256"]
    ):
        raise ValueError("r5_recovery_pinned_evidence_or_helper_changed")
    return receipt


def _claim(ctx, receipt, rows, frozen_path, ledger):
    if verify_prepared(ctx) != receipt:
        raise ValueError("r5_recovery_prepared_receipt_changed")
    fingerprint = canonical_hash(
        sorted((row["id"], row["hash"], row["writer"], row["label"]) for row in rows)
    )
    expected_ledger = Path(ctx["lineage"]["final_claim_ledger"])
    if (
        fingerprint != receipt["final_inventory_hash"]
        or Path(ledger).resolve() != expected_ledger.resolve()
        or any(row["split"] != "test" for row in rows)
        or digest_file(frozen_path) != receipt["original_winner_sha256"]
    ):
        raise ValueError("r5_recovery_claim_inventory_or_winner_changed")
    result = {
        "version": VERSION,
        "status": "explicit_owner_recovery_claimed_before_model_load",
        "claimed_at": datetime.now(timezone.utc).isoformat(),
        "final_inventory_hash": fingerprint,
        "frozen_winner_sha256": receipt["original_winner_sha256"],
        "original_claim_sha256": receipt["original_claim_sha256"],
        "recovery_manifest_sha256": digest_file(
            ctx["run"] / RETRY_DIRECTORY / "recovery-manifest.json"
        ),
        "helper_sha256": receipt["helper_sha256"],
        "retry_policy": "no_further_automatic_or_implicit_retries",
    }
    target = expected_ledger / (fingerprint + ".manual-retry-1.json")
    try:
        write_json(target, result)
    except FileExistsError:
        raise ValueError("r5_explicit_final_recovery_already_claimed") from None
    return result


def run(ctx, *, owner_run: bool, engine=None) -> dict:
    if owner_run is not True:
        raise ValueError("r5_recovery_requires_explicit_owner_run")
    receipt = verify_prepared(ctx)
    retry = ctx["run"] / RETRY_DIRECTORY
    claim_path = Path(ctx["lineage"]["final_claim_ledger"]) / (
        receipt["final_inventory_hash"] + ".manual-retry-1.json"
    )
    if (retry / "final-test").exists() or claim_path.exists():
        raise ValueError("r5_explicit_final_recovery_already_claimed_or_started")
    if engine is None:
        from research.english.r5_eval import execution as engine

    original_runtime = engine._runtime
    original_predict = engine.predict_rows
    original_score = engine.score_predictions
    original_claim = engine.claim_final_test

    def runtime(current):
        progress("r5-loading-frozen-model")
        value = original_runtime(current)
        progress("r5-frozen-model-loaded")
        return value

    def predict(torch, model, processor, tokenizer, rows, staging, **kwargs):
        observed_model = ProgressModel(model, len(rows))
        value = original_predict(
            torch, observed_model, processor, tokenizer, rows, staging, **kwargs
        )
        if observed_model.completed != len(rows) or len(value[0]) != len(rows):
            raise ValueError("r5_recovery_generation_inventory_incomplete")
        return value

    def score(rows, records, **kwargs):
        progress("r5-scoring-completed-predictions", samples=len(records))
        return original_score(rows, records, **kwargs)

    engine._runtime = runtime
    engine.predict_rows = predict
    engine.score_predictions = score
    engine.claim_final_test = lambda rows, winner, ledger: _claim(
        ctx, receipt, rows, winner, ledger
    )
    try:
        return engine.final_test({**ctx, "run": retry}, owner_run=True)
    finally:
        engine._runtime = original_runtime
        engine.predict_rows = original_predict
        engine.score_predictions = original_score
        engine.claim_final_test = original_claim


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("staging", "model", "initialization", "r4-winner", "run"):
        parser.add_argument("--" + name, type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    mode.add_argument("--owner-run", action="store_true")
    parser.add_argument("--using-frozen-source", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    paths = {
        key: getattr(args, key).expanduser()
        for key in ("staging", "model", "initialization", "r4_winner", "run")
    }
    if any(path.is_symlink() for path in paths.values()):
        raise ValueError("r5_recovery_inputs_must_not_be_symlink_roots")
    paths = {key: path.resolve() for key, path in paths.items()}
    if not args.using_frozen_source:
        from research.english.r5_eval.execution import private_run
        from research.english.r5_eval.snapshot import freeze_source

        private_run(paths["run"])
        progress("r5-verifying-frozen-source-and-data", model_execution=False)
        snapshot, manifest = freeze_source(paths["run"])
        helper = Path(__file__).resolve()
        if not args.prepare_only:
            retry = paths["run"] / RETRY_DIRECTORY
            receipt = json.loads(checked(retry, "recovery-manifest.json").read_text())
            helper = checked(retry, "recovery-runner.py")
            if digest_file(helper) != receipt.get("helper_sha256"):
                raise ValueError("r5_recovery_helper_changed")
        environment = {
            **os.environ,
            "PYTHONPATH": f"{snapshot / 'packages/contracts'}:{snapshot}",
            "PENFORGE_R5_SOURCE_MANIFEST": str(manifest),
            "PENFORGE_R5_LEDGER": json.loads(checked(paths["run"], "session.json").read_text())[
                "final_claim_ledger"
            ],
            "PYTHONUNBUFFERED": "1",
        }
        command = [sys.executable, "-u", str(helper), "--using-frozen-source"]
        for key, path in paths.items():
            command.extend(("--" + key.replace("_", "-"), str(path)))
        command.append(
            "--prepare-only"
            if args.prepare_only
            else "--verify-only"
            if args.verify_only
            else "--owner-run"
        )
        os.chdir(snapshot)
        os.execve(sys.executable, command, environment)
    from research.english.r5_eval import execution

    ctx = execution.context(**paths, include_test=True)
    if args.prepare_only:
        receipt = prepare(ctx)
        progress(
            "r5-final-recovery-prepared",
            model_execution=False,
            winner=receipt["winner"],
            output=str(paths["run"] / RETRY_DIRECTORY / "final-test"),
        )
        return
    if args.verify_only:
        receipt = verify_prepared(ctx)
        progress(
            "r5-final-recovery-verified",
            model_execution=False,
            winner=receipt["winner"],
            claim_created=False,
        )
        return
    result = run(ctx, owner_run=args.owner_run)
    print(
        json.dumps(
            {
                "status": result["status"],
                "winner": result["winner"],
                "groups": {
                    name: {
                        key: item["metrics"][key]
                        for key in (
                            "n",
                            "correct",
                            "accuracy",
                            "casefolded_shape_accuracy",
                            "writer_clustered_confidence_intervals",
                        )
                    }
                    for name, item in result["groups"].items()
                },
                "school_test": result["school_test"],
                "test_evaluated": result["test_evaluated"],
                "report": str(paths["run"] / RETRY_DIRECTORY / "final-test/report.json"),
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
