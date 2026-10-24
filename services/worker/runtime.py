"""The first real worker uses the queue interface shared with future stages.

This handler executes asset_verify v1; PipelineWorker dispatches it and Phase 04 analysis sequentially. No binary/user content is logged. The API
owns fenced publication and all state/quota transactions.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

from app.adapters.storage.base import ObjectStorage, StorageError
from app.core.errors import ApiError
from app.core.logging import log_event
from app.modules.jobs.service import checkpoint_store
from app.modules.uploads.service import QueueService, UploadsService, VerifiedAsset
from penforge_contracts import content_hash

from packages.capture_safety.decoder import (
    DecodeLimits,
    DecoderUnavailable,
    ImageRejected,
    VerifiedImage,
    verify_image,
)
from services.worker.checkpoints import dependencies


class LeaseLost(Exception):
    pass


class VerificationWorker:
    def __init__(
        self,
        queue: QueueService,
        uploads: UploadsService,
        storage: ObjectStorage,
        *,
        worker_id: str,
        limits: DecodeLimits | None = None,
        heartbeat_seconds: float = 20,
        temp_root: Path | None = None,
    ):
        self.queue, self.uploads, self.storage = queue, uploads, storage
        self.worker_id, self.limits = worker_id, limits or DecodeLimits()
        self.heartbeat_seconds, self.temp_root = heartbeat_seconds, temp_root
        self.stop = asyncio.Event()
        self.deadline = float("inf")

    async def run_once(self) -> bool:
        await self.queue.reap_expired()
        await self.reap_temporary()
        await self.uploads.reap_abandoned()
        job = await self.queue.claim(self.worker_id, worker_mem_mb=self.limits.memory_mb)
        if job is None:
            return False
        await self.execute(job)
        return True

    async def reap_temporary(self) -> int:
        """Only this worker's dedicated, inactive attempt directories are eligible."""
        if self.temp_root is None or not self.temp_root.exists():
            return 0
        count = 0
        for path in self.temp_root.iterdir():
            match = re.fullmatch(
                r"penforge-verify-([0-9a-f-]{36})-a([1-9][0-9]*)-[A-Za-z0-9_-]+", path.name
            )
            if (
                not match
                or path.is_symlink()
                or not path.is_dir()
                or path.stat().st_uid != os.getuid()
            ):
                continue
            if not await self.queue.attempt_active(UUID(match[1]), int(match[2])):
                await asyncio.to_thread(shutil.rmtree, path)
                count += 1
        return count

    async def execute(self, job: dict[str, Any]) -> bool:
        job_id, attempt, owner = job["id"], job["attempts"], job["owner_user_id"]
        asset = job["_asset"]
        lost = threading.Event()
        stage, completed = "hash", 0
        accepted = False
        publication_attempted = False
        publication_rejected = False
        derivative_key: str | None = None

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(min(0.5, self.heartbeat_seconds))
                try:
                    valid = await self.queue.heartbeat(
                        job_id,
                        self.worker_id,
                        attempt,
                        owner,
                        stage=stage,
                        completed_units=completed,
                    )
                except Exception:
                    valid = False
                if not valid:
                    lost.set()
                    return

        def check() -> None:
            if lost.is_set():
                raise LeaseLost

        async def advance(next_stage: str, count: int) -> None:
            nonlocal stage, completed
            check()
            stage, completed = next_stage, count
            if not await self.queue.heartbeat(
                job_id,
                self.worker_id,
                attempt,
                owner,
                stage=stage,
                completed_units=completed,
            ):
                lost.set()
                raise LeaseLost

        beat = asyncio.create_task(heartbeat())
        started = time.monotonic()
        try:
            if self.temp_root:
                self.temp_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix=f"penforge-verify-{job_id}-a{attempt}-", dir=self.temp_root
            ) as temporary:
                working = Path(temporary)
                source = working / "original"

                def copy_original() -> None:
                    total = 0
                    with source.open("xb") as stream:
                        for chunk in self.storage.iter_object(asset["storage_key"]):
                            check()
                            total += len(chunk)
                            if total > self.limits.max_bytes or total > asset["size_bytes"]:
                                raise ImageRejected("image_size_exceeded")
                            stream.write(chunk)
                    if total != asset["size_bytes"]:
                        raise ImageRejected("image_size_mismatch")

                await asyncio.to_thread(copy_original)
                await advance("decode", 1)
                checkpoints = checkpoint_store(self.uploads)
                with source.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                deps = dependencies(
                    ["sha256:" + digest], None, asdict(self.limits), "strict-image-verify-v1"
                )
                job["_failure_input_hash"] = content_hash(deps)
                cached = await checkpoints.load(job, self.worker_id, "verified-image", deps)
                if cached:
                    info = json.loads(cached["report"])
                    derived_path = working / "verified.png"
                    derived_path.write_bytes(cached["derivative"])
                    result = VerifiedImage(**info, derived_path=derived_path)
                else:
                    result = await asyncio.to_thread(
                        verify_image,
                        source,
                        declared_mime=asset["mime_type"],
                        expected_sha256=bytes(asset["declared_sha256"]).hex()
                        if asset["declared_sha256"] is not None
                        else None,
                        output_dir=working,
                        limits=self.limits,
                        cancel_check=check,
                    )
                    info = asdict(result)
                    info.pop("derived_path")
                    if not await checkpoints.save(
                        job,
                        self.worker_id,
                        "verified-image",
                        deps,
                        {
                            "report": (
                                "application/json",
                                json.dumps(info, sort_keys=True).encode(),
                            ),
                            "derivative": ("image/png", result.derived_path.read_bytes()),
                        },
                    ):
                        return False
                if self.stop.is_set() or time.monotonic() >= self.deadline:
                    from app.modules.jobs.service import JobsService

                    await JobsService(self.uploads).boundary(job, self.worker_id, shutdown=True)
                    return False
                await advance("derivative", 3)
                derivative_key = self.queue.derivative_key(job)

                with result.derived_path.open("rb") as stream:
                    if not await self.queue.stage_derivative(
                        job_id,
                        self.worker_id,
                        attempt,
                        owner,
                        chunks=iter(lambda: stream.read(65536), b""),
                        size_bytes=result.derived_size_bytes,
                        sha256=result.derived_sha256,
                    ):
                        lost.set()
                        raise LeaseLost
                await advance("publish", 3)
                from app.modules.jobs.service import measured_usage

                if not await measured_usage(
                    self.uploads, job, self.worker_id, time.monotonic() - started
                ):
                    return False
                publication_attempted = True
                publication = await self.queue.publish_verified(
                    job_id,
                    self.worker_id,
                    attempt,
                    owner,
                    VerifiedAsset(
                        sha256=result.original_sha256,
                        size_bytes=result.size_bytes,
                        mime_type=asset["mime_type"],
                        width_px=result.width_px,
                        height_px=result.height_px,
                        derivative_key=derivative_key,
                        derivative_size_bytes=result.derived_size_bytes,
                        derivative_sha256=result.derived_sha256,
                        derivative_width_px=result.derived_width_px,
                        derivative_height_px=result.derived_height_px,
                    ),
                )
                accepted = publication.accepted
                publication_rejected = not publication.accepted
                if accepted and publication.cleanup_keys:
                    await self.uploads.cleanup_asset(asset["id"])
                log_event(
                    "asset_verification_finished",
                    job_id=str(job_id),
                    stage="verified" if accepted else "lease_lost",
                    duration_ms=round((time.monotonic() - started) * 1000, 3),
                )
                return accepted
        except LeaseLost:
            return False
        except (ImageRejected, DecoderUnavailable, StorageError, OSError, ApiError) as error:
            retryable = isinstance(error, (DecoderUnavailable, StorageError, OSError)) or (
                isinstance(error, ApiError)
                and error.code in {"dependency_unavailable", "capacity_exceeded"}
            )
            if isinstance(error, ImageRejected):
                code = {
                    "image_size_exceeded": "upload_mismatch",
                    "image_size_mismatch": "upload_mismatch",
                    "image_hash_mismatch": "upload_mismatch",
                    "unsupported_image_magic": "unsupported_media_type",
                    "image_mime_mismatch": "unsupported_media_type",
                    "image_dimensions_exceeded": "image_dimensions_exceeded",
                    "image_pixels_exceeded": "image_dimensions_exceeded",
                    "image_frames_exceeded": "image_frames_exceeded",
                    "multiple_frames": "image_frames_exceeded",
                    "image_trailing_data": "image_trailing_data",
                    "decoder_resource_limit": "decode_resource_limit",
                    "decoder_wall_limit": "decode_resource_limit",
                    "decoder_cpu_limit": "decode_resource_limit",
                    "derived_size_exceeded": "decode_resource_limit",
                }.get(error.code, "image_decode_failed")
            else:
                code = error.code if isinstance(error, ApiError) else "dependency_unavailable"
            from app.modules.jobs.service import measured_usage

            await measured_usage(self.uploads, job, self.worker_id, time.monotonic() - started)
            failed = await self.queue.fail(
                job_id, self.worker_id, attempt, owner, code=code, retryable=retryable
            )
            if failed.accepted and failed.cleanup_keys:
                await self.uploads.cleanup_asset(asset["id"])
            return False
        finally:
            from app.modules.jobs.service import JobsService, measured_usage

            await measured_usage(self.uploads, job, self.worker_id, time.monotonic() - started)
            await JobsService(self.uploads).boundary(job, self.worker_id)
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
            # Attempt-specific output can never belong to another successful holder.
            # Never remove an accepted thumbnail; duplicate/failure cleanup is resumable.
            if (
                derivative_key
                and not accepted
                and (not publication_attempted or publication_rejected)
            ):
                with contextlib.suppress(StorageError, OSError):
                    await asyncio.to_thread(self.storage.delete_object, derivative_key)

    async def run(self, *, mode: str = "scheduled", max_seconds: float = 300) -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(name, self.stop.set)
        started, delay = time.monotonic(), 1.0
        self.deadline = started + max_seconds
        if isinstance(self, PipelineWorker):
            for handler in (self.analysis, self.exports):
                if handler:
                    handler.stop, handler.deadline = self.stop, self.deadline
        while not self.stop.is_set() and time.monotonic() - started < max_seconds:
            worked = await self.run_once()
            if worked:
                delay = 1.0
                continue
            if mode == "scheduled":
                break
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=delay)
            except TimeoutError:
                delay = min(15.0, delay * 2)


class PipelineWorker(VerificationWorker):
    """Fair sequential dispatch over the shared durable queue."""

    def __init__(self, *args, analysis, exports=None, privacy=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.privacy = privacy
        self._privacy_stuck_count = 0
        self.exports = exports
        self.analysis = analysis
        self.analysis_first = False

    async def run_once(self) -> bool:
        from app.modules.jobs.service import JobsService, queue_candidates

        if self.privacy:
            await self.privacy.reap()
            signals = await self.privacy.signals()
            if signals["stuck_requests"] != self._privacy_stuck_count:
                self._privacy_stuck_count = signals["stuck_requests"]
                if self._privacy_stuck_count:
                    log_event("privacy_saga_stuck", request_count=self._privacy_stuck_count)
        await JobsService(self.uploads).reap()
        await JobsService(self.uploads).reconcile_reservations()
        await self.reap_temporary()
        await self.analysis.reap_temporary()
        if self.exports:
            await self.exports.reap_temporary()
        await self.uploads.reap_abandoned()
        types: tuple[str, ...] = (
            ("asset_verify", "analyze_samples", "compile_export")
            if self.exports
            else ("asset_verify", "analyze_samples")
        )
        if self.privacy:
            types += ("privacy_maintenance",)
        # Dispatch the globally ordered eligible job, then claim it under the same
        # atomic scheduler protocol. A competing process may win; retry discovery.
        for _ in range(4):
            async with self.queue.engine.begin() as connection:
                candidates = await queue_candidates(
                    connection, types, (1, 2), self.limits.memory_mb
                )
            if not candidates:
                return False
            candidate = candidates[0]
            if candidate["job_type"] == "privacy_maintenance":
                job = await self.privacy.claim(
                    self.worker_id, worker_mem_mb=self.limits.memory_mb, job_id=candidate["id"]
                )
                if job:
                    await self.privacy.advance(job, self.worker_id)
                    return True
            elif candidate["job_type"] == "asset_verify":
                job = await self.queue.claim(
                    self.worker_id, worker_mem_mb=self.limits.memory_mb, job_id=candidate["id"]
                )
                if job:
                    await self.execute(job)
                    return True
            else:
                handler = (
                    self.analysis if candidate["job_type"] == "analyze_samples" else self.exports
                )
                job = await handler.service.claim(
                    handler.worker_id, worker_mem_mb=handler.memory_mb, job_id=candidate["id"]
                )
                if job:
                    await handler.execute(job)
                    return True
        return False
