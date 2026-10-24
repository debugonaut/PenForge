"""Separate R6 stages; defaults never start a model. All final execution is gated."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from research.english.r5_data.common import write_json
from research.english.r6_data.prepare import prepare
from research.english.r6_eval import execution, snapshot
from research.pretrained.artifacts import ROOT

STAGES = (
    "prepare",
    "preflight",
    "freeze-source",
    "control",
    "train",
    "resume",
    "select",
    "final-test",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--r5-staging", type=Path)
    for name in ("model", "r5-run", "run"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--owner-run", action="store_true")
    parser.add_argument("--using-frozen-source", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.umask(0o077)
    # Never even enter an evaluation runtime without a separately eligible inventory.
    if args.stage == "final-test":
        raise ValueError("r6_fresh_final_unavailable_no_test_claim_or_model_load_permitted")
    if args.stage in {"train", "resume"}:
        execution.require_execution(args.owner_run)
    raw = {key: getattr(args, key).expanduser() for key in ("staging", "model", "r5_run", "run")}
    if any(p.is_symlink() for p in raw.values()):
        raise ValueError("r6_symlink_input_root")
    paths = {key: path.resolve() for key, path in raw.items()}
    if args.stage == "prepare":
        if args.r5_staging is None:
            raise ValueError("r6_prepare_requires_existing_r5_staging")
        value = prepare(args.r5_staging, paths["staging"])
        print(json.dumps({"status": value["status"], "model_execution": False}), flush=True)
        return
    if args.stage not in {"preflight", "freeze-source"} and not args.using_frozen_source:
        target, _ = snapshot.verify(paths["run"])
        environment = {
            **os.environ,
            "PYTHONPATH": f"{target / 'packages/contracts'}:{target}",
            "PYTHONUNBUFFERED": "1",
            "PENFORGE_R6_ORIGIN_ROOT": str(ROOT),
        }
        command = [
            sys.executable,
            "-u",
            "-m",
            "research.english.r6_run",
            args.stage,
            "--using-frozen-source",
        ]
        for name, path in paths.items():
            command.extend(("--" + name.replace("_", "-"), str(path)))
        if args.owner_run:
            command.append("--owner-run")
        os.chdir(target)
        os.execve(sys.executable, command, environment)
    started_wall, started_cpu = time.monotonic(), time.process_time()
    ctx = execution.data_context(paths["staging"], paths["model"], paths["r5_run"], paths["run"])
    prepared = execution.preflight(ctx)
    run = ctx["run"]
    if args.using_frozen_source:
        _, manifest = snapshot.verify(run)
        if Path(manifest["snapshot_root"]).resolve() != ROOT:
            raise ValueError("r6_execution_must_use_the_verified_frozen_source_root")
    if args.stage == "preflight":
        value = {
            "status": prepared["status"],
            "model_execution": False,
            "training_samples": 6656,
            "validation_samples": 1664,
            "test_rows_passed_to_part_b": 0,
        }
    elif args.stage == "freeze-source":
        manifest = snapshot.freeze(run, ready=True)
        value = {
            "status": "r6_complete_executable_source_frozen",
            "source_files": len(manifest["files"]),
            "model_execution": False,
        }
    elif args.stage == "control":
        value = execution.control(ctx)
    elif args.stage in {"train", "resume"}:
        value = execution.train(
            ctx, prepared["model"], owner_run=args.owner_run, resume=args.stage == "resume"
        )
    else:
        value = execution.freeze_selection(ctx, prepared["model"]["prepared"])
    # Reserve 600 seconds outside the tighter native model limit, cumulative across
    # metadata/control/selection invocations; hard-killed model attempts are charged by B.
    if args.stage not in {"train", "resume"}:
        ledger = run / "integration-consumption.json"
        consumed = (
            json.loads(ledger.read_text())
            if ledger.exists()
            else {"wall_seconds": 0.0, "cpu_seconds": 0.0}
        )
        consumed["wall_seconds"] += time.monotonic() - started_wall
        consumed["cpu_seconds"] += time.process_time() - started_cpu
        if max(consumed.values()) > 600:
            raise ValueError("r6_integration_overhead_reserve_exhausted")
        temporary = ledger.with_suffix(".new")
        write_json(temporary, consumed)
        temporary.replace(ledger)
    print(
        json.dumps(
            {
                "status": value["status"],
                "test_evaluated": False,
                "model_execution": args.stage in {"train", "resume"},
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, FileExistsError) as error:
        print(
            json.dumps({"status": "refused", "reason": str(error), "automatic_retry": False}),
            file=sys.stderr,
            flush=True,
        )
        sys.exit(2)
