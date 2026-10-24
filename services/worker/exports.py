"""Real sequential compile/validate under the shared durable lease protocol."""

import asyncio
import contextlib
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from uuid import UUID

from app.adapters.storage.base import StorageError
from app.core.errors import ApiError
from app.modules.exports.service import encoded
from app.modules.jobs.service import checkpoint_store
from penforge_contracts import content_hash
from penforge_font_engine.isolated import compile_isolated

from packages.capture_safety.decoder import DecoderUnavailable
from services.worker.checkpoints import dependencies


class ExportWorker:
    def __init__(self, service, *, worker_id, temp_root, heartbeat_seconds=20, memory_mb=768):
        self.service, self.worker_id, self.temp_root = service, worker_id, Path(temp_root)
        self.stop = asyncio.Event()
        self.deadline = float("inf")
        self.heartbeat_seconds, self.memory_mb = heartbeat_seconds, memory_mb

    async def reap_temporary(self):
        self.temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in self.temp_root.iterdir():
            match = re.fullmatch(
                r"penforge-export-([0-9a-f-]{36})-a([1-9][0-9]*)-[A-Za-z0-9_-]+", path.name
            )
            if (
                match
                and not path.is_symlink()
                and path.is_dir()
                and path.stat().st_uid == os.getuid()
            ):
                if not await self.service.attempt_active(UUID(match[1]), int(match[2])):
                    await asyncio.to_thread(shutil.rmtree, path)

    async def run_once(self):
        await self.reap_temporary()
        job = await self.service.claim(self.worker_id, worker_mem_mb=self.memory_mb)
        if job is None:
            return False
        await self.execute(job)
        return True

    async def execute(self, job):
        lost = threading.Event()
        stage = "compiling"
        completed = 0
        total = len(job["_exports"])
        started = time.monotonic()

        def check():
            if lost.is_set():
                raise ValueError("lease_lost")

        def progress(value):
            nonlocal stage, completed, total
            stage = "validating"
            completed = value["completed_units"]
            total = 5

        async def beat():
            while True:
                await asyncio.sleep(min(0.5, self.heartbeat_seconds))
                try:
                    valid = await self.service.heartbeat(
                        job, self.worker_id, stage, completed, total
                    )
                except Exception:
                    valid = False
                if not valid:
                    lost.set()
                    return

        heartbeat = asyncio.create_task(beat())
        try:
            outputs = {}
            reports = {}
            self.temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=f"penforge-export-{job['id']}-a{job['attempts']}-", dir=self.temp_root
            ) as directory:
                for index, row in enumerate(job["_exports"]):
                    check()
                    if self.stop.is_set() or time.monotonic() >= self.deadline:
                        from app.modules.jobs.service import JobsService

                        await JobsService(self.service.uploads).boundary(
                            job, self.worker_id, shutdown=True
                        )
                        return False
                    stage = "compiling"
                    completed = index
                    total = len(job["_exports"])
                    if not await self.service.heartbeat(
                        job, self.worker_id, stage, completed, total
                    ):
                        return False
                    checkpoints = checkpoint_store(self.service.uploads)
                    deps = dependencies(
                        [row["build_manifest"]], None, row["build_manifest"], "font-compile-v1"
                    )
                    job["_failure_input_hash"] = content_hash(deps)
                    cached = await checkpoints.load(
                        job, self.worker_id, f"format-{row['format']}", deps
                    )
                    if cached:
                        import json

                        font, report = cached.get("font", b""), json.loads(cached["report"])
                    else:
                        font, report = await asyncio.to_thread(
                            compile_isolated,
                            Path(directory) / str(index),
                            row["build_manifest"],
                            job["_glyphs"],
                            row["format"],
                            cancel_check=check,
                            progress=progress,
                            memory_mb=self.memory_mb,
                        )
                        outputs_for_checkpoint = {"report": ("application/json", encoded(report))}
                        if report["status"] != "failed":
                            outputs_for_checkpoint["font"] = (f"font/{row['format']}", font)
                        if not await checkpoints.save(
                            job,
                            self.worker_id,
                            f"format-{row['format']}",
                            deps,
                            outputs_for_checkpoint,
                        ):
                            return False
                    context = job.get("_coverage_context", {})
                    report["coverage"]["generated_pending"] = context.get("generated_pending", [])
                    report["coverage"]["generated_pending_observation"] = context.get(
                        "observation", "unknown"
                    )
                    plans = job["checkpoint"]["outputs"][str(row["id"])]
                    reports[str(row["id"])] = report
                    outputs[UUID(plans["report"])] = encoded(report)
                    if report["status"] != "failed":
                        outputs[UUID(plans["font"])] = font
                check()
                if not await self.service.stage(job, self.worker_id, outputs):
                    return False
                return await self.service.publish(
                    job, self.worker_id, reports, time.monotonic() - started
                )
        except (ValueError, OSError, ApiError, StorageError, DecoderUnavailable) as error:
            resource_shortage = isinstance(error, ValueError) and str(error) in {
                "font_resource_limit",
                "isolation_unavailable",
            }
            retryable = (
                resource_shortage
                or isinstance(error, (StorageError, OSError, DecoderUnavailable))
                or isinstance(error, ApiError)
                and error.status == 503
            )
            await self.service.fail(
                job,
                self.worker_id,
                "resource_unavailable"
                if resource_shortage
                else "dependency_unavailable"
                if retryable
                else "validation_failed",
                retryable=retryable,
                usage_seconds=time.monotonic() - started,
            )
            return False
        finally:
            from app.modules.jobs.service import JobsService, measured_usage

            await measured_usage(
                self.service.uploads, job, self.worker_id, time.monotonic() - started
            )
            await JobsService(self.service.uploads).boundary(job, self.worker_id)
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
