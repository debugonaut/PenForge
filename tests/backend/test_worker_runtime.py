"""Exercise the actual worker, decoder and PostgreSQL after process loss."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.adapters.storage.base import StorageError
from app.core.errors import ApiError
from app.db import models as m
from PIL import Image
from test_uploads_database import configure, create_project, execute, init_uploaded, principal
from test_uploads_database import uploads_database as uploads_database

from packages.capture_safety import DecodeLimits
from services.worker.runtime import VerificationWorker

pytestmark = pytest.mark.integration


def png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (64, 48), "ivory").save(stream, format="PNG")
    return stream.getvalue()


def test_actual_worker_decodes_preserves_original_and_retains_ambiguous_committed_result(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        data = png()
        asset_id, completed = await init_uploaded(uploads, owner, project, data)
        publish = queue.publish_verified

        async def interrupted_response(*args, **kwargs):
            result = await publish(*args, **kwargs)
            assert result.accepted
            raise ApiError("dependency_unavailable", 503)

        queue.publish_verified = interrupted_response
        worker = VerificationWorker(
            queue,
            uploads,
            storage,
            worker_id="actual-worker",
            heartbeat_seconds=0.1,
            temp_root=tmp_path / "worker-temp",
        )
        assert await worker.run_once()
        asset = await uploads.get(owner, asset_id)
        job = await uploads.get_job(owner, uuid.UUID(completed["job_id"]))
        assert asset["state"] == "ready" and job["state"] == "succeeded"
        assert asset["sha256"] == "sha256:" + hashlib.sha256(data).hexdigest()
        with sync.connect() as connection:
            rows = connection.execute(sa.select(m.assets)).mappings().all()
        original = next(row for row in rows if row["kind"] == "original_upload")
        thumb = next(row for row in rows if row["kind"] == "thumbnail")
        assert storage.read_object(original["storage_key"], max_bytes=len(data)) == data
        derived = storage.read_object(thumb["storage_key"], max_bytes=1024)
        with Image.open(io.BytesIO(derived)) as decoded:
            decoded.load()
            assert not decoded.getexif() and not decoded.info
        assert not list((tmp_path / "worker-temp").iterdir())
        assert (await uploads.reconcile())["orphan_keys"] == []
        assert not await worker.run_once()

    execute(uploads_database, tmp_path, scenario)


def test_two_actual_workers_claim_distinct_jobs(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        await init_uploaded(uploads, owner, project, png())
        # Separate source hash avoids testing dedupe instead of independent claims.
        stream = io.BytesIO()
        Image.new("RGB", (64, 48), "coral").save(stream, "PNG")
        await init_uploaded(uploads, owner, project, stream.getvalue())
        workers = [
            VerificationWorker(
                queue,
                uploads,
                storage,
                worker_id=f"parallel-{i}",
                heartbeat_seconds=0.1,
                temp_root=tmp_path / "worker-temp",
            )
            for i in range(2)
        ]
        assert all(await asyncio.gather(*(worker.run_once() for worker in workers)))
        with sync.connect() as connection:
            jobs = connection.execute(sa.select(m.jobs)).mappings().all()
        assert len(jobs) == 2
        assert all(job["state"] == "succeeded" and job["attempts"] == 1 for job in jobs)
        assert (await uploads.reconcile())["orphan_keys"] == []

    execute(uploads_database, tmp_path, scenario)


def test_transient_staging_storage_failure_requeues_instead_of_rejecting_source(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        asset_id, completed = await init_uploaded(uploads, owner, project, png())
        write = storage.put_immutable_stream
        failures = 0

        def unavailable_once(*args, **kwargs):
            nonlocal failures
            if failures == 0:
                failures += 1
                raise StorageError("synthetic_transient_failure")
            return write(*args, **kwargs)

        storage.put_immutable_stream = unavailable_once
        worker = VerificationWorker(
            queue,
            uploads,
            storage,
            worker_id="retry-worker",
            heartbeat_seconds=0.1,
            temp_root=tmp_path / "worker-temp",
        )
        assert await worker.run_once()
        job_id = uuid.UUID(completed["job_id"])
        job = await uploads.get_job(owner, job_id)
        assert job["state"] == "queued" and job["error_code"] == "dependency_unavailable"
        assert (await uploads.get(owner, asset_id))["state"] == "verifying"
        assert await worker.run_once()
        job = await uploads.get_job(owner, job_id)
        assert job["state"] == "succeeded" and job["attempts"] == 2
        assert (await uploads.reconcile())["orphan_keys"] == []

    execute(uploads_database, tmp_path, scenario)


def test_sigkill_after_derivative_then_expired_lease_retries_without_orphan_or_quota_loss(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        data = png()
        asset_id, completed = await init_uploaded(uploads, owner, project, data)
        temp_root = tmp_path / "worker-temp"
        marker = tmp_path / "derivative-written"
        child = tmp_path / "crash_worker.py"
        # A barrier after the real immutable write gives a deterministic kill point.
        child.write_text("""
import asyncio,sys,time
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from app.adapters.storage.local_fs import LocalObjectStorage
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import UploadsService,QueueService
from services.worker.runtime import VerificationWorker
class PausedStorage(LocalObjectStorage):
    def put_immutable_stream(self,*args,**kwargs):
        result=super().put_immutable_stream(*args,**kwargs)
        Path(sys.argv[4]).write_text("written")
        time.sleep(30)
        return result
async def main():
    engine=create_async_engine(sys.argv[1],connect_args={"options":"-csearch_path="+sys.argv[2]})
    storage=PausedStorage(Path(sys.argv[3]),secret=b"s"*32)
    uploads=UploadsService(engine,IdentityService(engine),storage,derivative_max_bytes=1024)
    queue=QueueService(engine,uploads,lease_seconds=1,retry_backoff_seconds=0)
    worker=VerificationWorker(queue,uploads,storage,worker_id="kill-target",heartbeat_seconds=.1,temp_root=Path(sys.argv[5]))
    await worker.run_once()
asyncio.run(main())
""")
        process = subprocess.Popen(
            [
                sys.executable,
                str(child),
                sync.url.render_as_string(hide_password=False),
                uploads_database[1],
                str(storage.root),
                str(marker),
                str(temp_root),
            ],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [
                        str(Path.cwd() / "apps/api"),
                        str(Path.cwd() / "packages/contracts"),
                        str(Path.cwd()),
                    ]
                ),
            },
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not marker.exists():
                if process.poll() is not None:
                    raise AssertionError(
                        (process.stderr.read() if process.stderr else b"").decode()
                    )
                assert time.monotonic() < deadline, "worker did not reach crash barrier"
                await asyncio.sleep(0.02)
            process.send_signal(signal.SIGKILL)
            await asyncio.to_thread(process.wait, 5)
            assert process.returncode == -signal.SIGKILL
            assert list(temp_root.iterdir()), "crash must leave real attempt files"
            deadline = time.monotonic() + 4
            while await queue.attempt_active(uuid.UUID(completed["job_id"]), 1):
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
            worker = VerificationWorker(
                queue,
                uploads,
                storage,
                worker_id="recovery-worker",
                heartbeat_seconds=0.1,
                temp_root=temp_root,
                limits=DecodeLimits(),
            )
            assert await worker.run_once()
            job = await uploads.get_job(owner, uuid.UUID(completed["job_id"]))
            assert job["state"] == "succeeded" and job["attempts"] == 2
            assert (await uploads.get(owner, asset_id))["state"] == "ready"
            assert not list(temp_root.iterdir())
            inventory = storage.list_objects()
            assert len([o for o in inventory if not o.key.startswith("checkpoints/")]) == 2
            assert (await uploads.reconcile())["orphan_keys"] == []
            physical = sum(item.size_bytes for item in inventory)
            with sync.connect() as connection:
                balances = (
                    connection.execute(
                        sa.select(m.quota_balances).where(
                            m.quota_balances.c.resource == "storage_bytes"
                        )
                    )
                    .mappings()
                    .all()
                )
            assert all(
                balance["reserved"] == 0 and balance["committed"] == physical
                for balance in balances
            )
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
            if process.stderr:
                process.stderr.close()

    execute(uploads_database, tmp_path, scenario, lease=1)
