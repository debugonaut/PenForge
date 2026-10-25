"""Phase 06 real PostgreSQL races and durable protocol regressions."""

import asyncio
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.db import models as m
from test_uploads_database import configure, create_project, execute, init_uploaded, principal
from test_uploads_database import uploads_database as uploads_database

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def prepare_evidence_directory():
    # Evidence is generated on each clean checkout; no prior local run is required.
    Path("artifacts/phase06").mkdir(parents=True, exist_ok=True)


async def queue_config(engine, **values):
    from sqlalchemy.dialects.postgresql import insert

    async with engine.begin() as con:
        await con.execute(
            insert(m.system_settings)
            .values(key="queue_policy.v1", value={"retry_base": 0, **values})
            .on_conflict_do_update(
                index_elements=[m.system_settings.c.key],
                set_={"value": {"retry_base": 0, **values}},
            )
        )


def test_many_workers_strict_caps_and_owner_fairness(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owners = []
        for _ in range(3):
            person = principal()
            user, project = await create_project(identity, projects, person)
            await configure(engine, user)
            owners.append(user)
            for _ in range(4):
                await init_uploaded(uploads, person, project)
        await queue_config(engine, global_running=3, owner_running=1)
        claims = await asyncio.gather(*(queue.claim(f"worker-{i}") for i in range(20)))
        claimed = [j for j in claims if j]
        assert len(claimed) == 3
        assert len({j["id"] for j in claimed}) == 3
        assert {j["owner_user_id"] for j in claimed} == set(owners)
        assert all(j["attempts"] == 1 for j in claimed)

    execute(uploads_database, tmp_path, scenario)


def test_unsupported_schema_script_profile_and_aging(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person = principal()
        user, project = await create_project(identity, projects, person)
        await configure(engine, user)
        ids = []
        for _ in range(4):
            _, result = await init_uploaded(uploads, person, project)
            ids.append(uuid.UUID(result["job_id"]))
        async with engine.begin() as con:
            await con.execute(
                m.jobs.update().where(m.jobs.c.id == ids[0]).values(payload_schema_version=2)
            )
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == ids[1])
                .values(resource_profile={"mem_mb": "bogus"})
            )
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == ids[2])
                .values(priority=-2, created_at=sa.func.now() - timedelta(hours=1))
            )
            await con.execute(m.jobs.update().where(m.jobs.c.id == ids[3]).values(priority=2))
        claim = await queue.claim("compatible-n-minus-one")
        assert claim["id"] == ids[2]  # Old bounded low priority is not starved.
        assert (await queue.claim("second"))["id"] == ids[3]
        assert await queue.claim("no-compatible-capacity") is None

    execute(uploads_database, tmp_path, scenario)


def test_checkpoint_verified_reuse_dependency_mismatch_corruption_and_fencing(
    uploads_database, tmp_path
):
    from app.core.errors import ApiError
    from app.modules.jobs.service import checkpoint_store

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person = principal()
        user, project = await create_project(identity, projects, person)
        await configure(engine, user)
        await init_uploaded(uploads, person, project)
        job = await queue.claim("checkpoint-owner")
        store = checkpoint_store(uploads)
        deps = dict(
            inputs=["sha256:" + "1" * 64],
            template=None,
            model=None,
            configuration={},
            algorithm="v1",
            dependencies="sha256:" + "2" * 64,
        )
        assert await store.save(
            job, "checkpoint-owner", "page-2", deps, {"output": ("application/json", b'{"unit":2}')}
        )
        assert await store.load(job, "checkpoint-owner", "page-2", deps) == {
            "output": b'{"unit":2}'
        }
        assert (
            await store.load(job, "checkpoint-owner", "page-2", deps | {"algorithm": "v2"}) is None
        )
        assert await store.load(job, "stale-holder", "page-2", deps) is None
        async with engine.connect() as con:
            key = await con.scalar(
                sa.select(m.assets.c.storage_key)
                .where(m.assets.c.kind == "checkpoint", m.assets.c.mime_type == "application/json")
                .order_by(m.assets.c.created_at)
                .limit(1)
            )
        storage.delete_object(key)
        storage.put_immutable(key, b"corrupt")
        with pytest.raises(ApiError, match="checkpoint_invalid"):
            await store.load(job, "checkpoint-owner", "page-2", deps)
        async with engine.begin() as con:
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(lease_expires_at=sa.func.clock_timestamp() - timedelta(seconds=1))
            )
        assert not await store.save(
            job, "checkpoint-owner", "page-3", deps, {"output": ("application/json", b"{}")}
        )

    execute(uploads_database, tmp_path, scenario)


def test_real_sigkill_after_second_unit_resumes_third_verified_unit(uploads_database, tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    from app.modules.analysis.schemas import AnalysisCreate
    from app.modules.analysis.service import AnalysisService
    from app.modules.uploads.schemas import StrokeSampleCreate
    from penforge_contracts import StrokeDocument
    from test_analysis_database import drawing

    from services.worker import analysis as runtime

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, sample, body = await drawing(engine, identity, projects, uploads)
        doc = StrokeDocument.model_validate_json(
            Path("packages/contracts/fixtures/strokes.v1.json").read_bytes()
        )
        samples, assignments = list(body.sample_ids), list(body.assignments)
        for cp in (104, 105):
            _, sample2 = await uploads.register_sample(
                owner,
                project,
                idempotency_key=uuid.uuid4().hex,
                body=StrokeSampleCreate(source_kind="strokes", stroke_document=doc),
            )
            samples.append(sample2["sample_id"])
            assignments.append(
                dict(
                    sample_id=sample2["sample_id"],
                    codepoints=[cp],
                    capture_transform=dict(baseline_y=0.75, units_per_px=1.0, pen_width_px=3.0),
                )
            )
        api = AnalysisService(engine, identity, uploads, storage)
        _, receipt = await api.create(
            owner,
            project,
            uuid.uuid4().hex,
            AnalysisCreate(sample_ids=samples, assignments=assignments),
        )
        marker = tmp_path / "second-unit-committed"
        script = tmp_path / "checkpoint_worker.py"
        script.write_text("""import asyncio,sys
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from app.adapters.storage.local_fs import LocalObjectStorage
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import UploadsService
from app.modules.analysis.service import AnalysisService
from app.modules.jobs.checkpoints import Checkpoints
from services.worker.analysis import AnalysisWorker
save=Checkpoints.save
async def barrier(self,job,holder,stage,*args):
 result=await save(self,job,holder,stage,*args)
 if result and stage=='sample-1':
  Path(sys.argv[4]).write_text('two verified units')
  await asyncio.sleep(60)
 return result
Checkpoints.save=barrier
async def main():
 engine=create_async_engine(sys.argv[1],connect_args={'options':'-csearch_path='+sys.argv[2]})
 storage=LocalObjectStorage(Path(sys.argv[3]),secret=b's'*32)
 identity=IdentityService(engine); uploads=UploadsService(engine,identity,storage)
 service=AnalysisService(engine,identity,uploads,storage)
 await AnalysisWorker(service,worker_id='kill-after-two',temp_root=Path(sys.argv[5]),heartbeat_seconds=.1).run_once()
asyncio.run(main())
""")
        process = subprocess.Popen(
            [
                sys.executable,
                str(script),
                sync.url.render_as_string(hide_password=False),
                uploads_database[1],
                str(storage.root),
                str(marker),
                str(tmp_path / "attempts"),
            ],
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 15
            while not marker.exists():
                if process.poll() is not None:
                    raise AssertionError(process.stderr.read().decode())
                assert time.monotonic() < deadline
                await asyncio.sleep(0.02)
            process.send_signal(signal.SIGKILL)
            await asyncio.to_thread(process.wait, 5)
            assert process.returncode == -signal.SIGKILL
            async with engine.begin() as con:
                await con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == uuid.UUID(receipt["job_id"]))
                    .values(lease_expires_at=sa.func.clock_timestamp() - timedelta(seconds=1))
                )
            calls = []
            original = runtime.analyze_isolated

            def observed(*args, **kwargs):
                calls.append(True)
                return original(*args, **kwargs)

            runtime.analyze_isolated = observed
            try:
                worker = runtime.AnalysisWorker(
                    api,
                    worker_id="resume-at-three",
                    temp_root=tmp_path / "attempts",
                    heartbeat_seconds=0.1,
                )
                assert await worker.run_once()
            finally:
                runtime.analyze_isolated = original
            assert len(calls) == 1
            final = await api.get(owner, project, uuid.UUID(receipt["job_id"]))
            assert final["state"] == "succeeded" and final["attempts"] == 2
            assert (await uploads.reconcile())["orphan_keys"] == []
            async with engine.connect() as con:
                assert (
                    await con.scalar(sa.select(sa.func.count()).select_from(m.job_checkpoints)) == 3
                )
                assert (
                    await con.scalar(sa.select(sa.func.count()).select_from(m.glyph_candidates))
                    == 3
                )
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
            process.stderr.close()

    execute(uploads_database, tmp_path, scenario)


def test_retry_jitter_poison_and_cancel_settle_once(uploads_database, tmp_path):
    from app.modules.jobs.service import JobsService, backoff

    assert backoff(1, random_source=lambda: 0) == 24
    assert backoff(1, random_source=lambda: 1) == pytest.approx(36)
    assert backoff(100, random_source=lambda: 1) == 600

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        _, receipt = await init_uploaded(uploads, owner, project)
        job = await queue.claim("retry")
        jobs = JobsService(uploads, random_source=lambda: 0.5)
        assert await jobs.retry_or_fail(
            job, "retry", "dependency_unavailable", retryable=True, base=0
        )
        retried = await queue.claim("retry-2")
        assert retried["attempts"] == 2
        assert await jobs.retry_or_fail(
            retried, "retry-2", "dependency_unavailable", retryable=True, base=0
        )
        final = await jobs.get(owner, job["id"])
        assert final["state"] == "failed" and final["error_code"] == "poison_suspected"
        _, receipt = await init_uploaded(uploads, owner, project)
        queued_id = uuid.UUID(receipt["job_id"])
        assert (await jobs.cancel(owner, queued_id))["state"] == "canceled"
        assert (await jobs.cancel(owner, queued_id))["state"] == "canceled"
        await init_uploaded(uploads, owner, project)
        running = await queue.claim("cancel")
        assert (await jobs.cancel(owner, running["id"]))["state"] == "cancel_requested"
        assert not await queue.heartbeat(
            running["id"], "cancel", 1, user, stage="decode", completed_units=1
        )
        assert await jobs.boundary(running, "cancel")
        assert not await jobs.boundary(running, "cancel")
        assert (await jobs.get(owner, running["id"]))["state"] == "canceled"
        async with engine.connect() as con:
            assert all(
                r["reserved"] == 0
                for r in (await con.execute(sa.select(m.quota_balances))).mappings()
            )

    execute(uploads_database, tmp_path, scenario)


def test_graceful_boundary_retains_monotonic_fence_without_failure_charge(
    uploads_database, tmp_path
):
    from app.modules.jobs.service import JobsService

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        await init_uploaded(uploads, owner, project)
        job = await queue.claim("same-process")
        assert await JobsService(uploads).boundary(job, "same-process", shutdown=True)
        replacement = await queue.claim("same-process")
        assert replacement["attempts"] == job["attempts"] + 1
        assert replacement["max_attempts"] == job["max_attempts"] + 1
        assert not await queue.heartbeat(
            job["id"], "same-process", job["attempts"], user, stage="hash", completed_units=0
        )

    execute(uploads_database, tmp_path, scenario)


def test_concurrent_user_global_reservations_no_overrun_and_overage_fails_atomic(
    uploads_database, tmp_path
):
    from app.core.errors import ApiError
    from app.modules.jobs.service import JobsService, measured_usage, settle
    from test_uploads_database import body

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        pending = []
        for _ in range(4):
            person = principal()
            user, project = await create_project(identity, projects, person)
            await configure(engine, user)
            _, value = await uploads.initialize(
                person, project, idempotency_key=uuid.uuid4().hex, body=body()
            )
            aid = uuid.UUID(value["asset_id"])
            await uploads.put_capability(
                value["upload"]["url"].rsplit("/", 1)[1], __import__("test_uploads_database").SOURCE
            )
            pending.append((person, aid))
        async with engine.begin() as con:
            await con.execute(
                m.quota_balances.update()
                .where(
                    m.quota_balances.c.scope == "global",
                    m.quota_balances.c.resource == "compute_seconds",
                )
                .values(limit_amount=360)
            )
        results = await asyncio.gather(
            *(uploads.complete(person, aid) for person, aid in pending), return_exceptions=True
        )
        successes = [(pending[i][0], r) for i, r in enumerate(results) if isinstance(r, dict)]
        assert len(successes) == 2
        assert all(
            isinstance(r, dict) or isinstance(r, ApiError) and r.code == "quota_exceeded"
            for r in results
        )
        async with engine.connect() as con:
            rows = list((await con.execute(sa.select(m.quota_balances))).mappings())
            assert all(r["committed"] + r["reserved"] <= r["limit_amount"] for r in rows)
            assert (
                next(
                    r for r in rows if r["scope"] == "global" and r["resource"] == "compute_seconds"
                )["reserved"]
                == 360
            )
        job = await queue.claim("usage")
        with pytest.raises(ApiError, match="quota_exceeded"):
            async with engine.begin() as con:
                await settle(con, job, {"compute_seconds": 181})
        assert await measured_usage(uploads, job, "usage", 181)
        jobs = JobsService(uploads)
        for person, result in successes:
            await jobs.cancel(person, uuid.UUID(result["job_id"]))
        if job:
            assert await jobs.boundary(job, "usage")
        async with engine.connect() as con:
            final = (
                (await con.execute(sa.select(m.jobs).where(m.jobs.c.id == job["id"])))
                .mappings()
                .one()
            )
            assert final["error_code"] == "quota_exceeded"
            assert final["usage"]["wall_seconds"] == 181
            assert final["usage"]["uncommitted_compute_seconds"] == 1
            assert final["usage"]["reservation_exceeded"] is True
            balances = list((await con.execute(sa.select(m.quota_balances))).mappings())
            assert all(b["committed"] + b["reserved"] <= b["limit_amount"] for b in balances)
            global_compute = next(
                b for b in balances if b["scope"] == "global" and b["resource"] == "compute_seconds"
            )
            assert global_compute["committed"] == 180 and global_compute["reserved"] == 0
        await jobs.reconcile_reservations()
        assert (await jobs.reconcile_reservations())["released_orphan_reservations"] == 0

    execute(uploads_database, tmp_path, scenario)


def test_degradation_rejects_new_work_preserves_reads_and_accuracy(uploads_database, tmp_path):
    from app.core.errors import ApiError
    from app.modules.jobs.service import JobsService

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        _, receipt = await init_uploaded(uploads, owner, project)
        await queue_config(engine, degradation_level=4)
        with pytest.raises(ApiError) as blocked:
            await init_uploaded(uploads, owner, project)
        assert blocked.value.code == "capacity_exceeded"
        assert blocked.value.headers["Retry-After"] == "5"
        view = await JobsService(uploads).get(owner, uuid.UUID(receipt["job_id"]))
        assert view["quality_policy"] == "accuracy_first" and view["eta_seconds"] is None
        assert view["eta_reason"] == "insufficient_data"
        # Existing qualified work is still processed; no algorithm substitution.
        assert await queue.claim("qualified")

    execute(uploads_database, tmp_path, scenario)


@pytest.mark.parametrize("workers", [1, 5, 20])
def test_measured_concurrency_fairness_and_explain(uploads_database, tmp_path, workers):
    import json
    import platform
    import time
    from pathlib import Path

    from app.modules.jobs.policy import QueuePolicy
    from app.modules.jobs.repository import claim_query
    from app.modules.jobs.service import JobsService
    from penforge_contracts import content_hash

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        people = {}
        for index in range(5):
            person = principal()
            user, project = await create_project(identity, projects, person)
            people[user] = person
            await configure(engine, user)
            for _ in range(6 if index == 0 else 2):
                await init_uploaded(uploads, person, project)
        await queue_config(engine, global_running=4, owner_running=1)
        records, order, beats = [], [], []
        jobs = JobsService(uploads)
        while True:

            async def claim(i):
                began = time.perf_counter()
                result = await queue.claim(f"measured-{i}")
                records.append((time.perf_counter() - began) * 1000)
                return result

            rows = [j for j in await asyncio.gather(*(claim(i) for i in range(workers))) if j]
            if not rows:
                break
            assert len(rows) <= 4 and len({j["owner_user_id"] for j in rows}) == len(rows)
            for job in sorted(rows, key=lambda j: j["last_heartbeat_at"]):
                order.append(str(job["owner_user_id"]))
                began = time.perf_counter()
                assert await queue.heartbeat(
                    job["id"],
                    job["lease_holder"],
                    job["attempts"],
                    job["owner_user_id"],
                    stage="decode",
                    completed_units=1,
                )
                beats.append((time.perf_counter() - began) * 1000)
                await jobs.cancel(people[job["owner_user_id"]], job["id"])
                assert await jobs.boundary(job, job["lease_holder"])
        assert len(set(order[:5])) == 5
        assert len(order) == 14
        with sync.begin() as con:
            con.exec_driver_sql("SET LOCAL enable_seqscan=off")
            query = claim_query(("asset_verify",), (1,), 768, QueuePolicy()).compile(
                dialect=con.dialect, compile_kwargs={"literal_binds": True}
            )
            explain = con.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(query)
            ).scalar_one()
            reaper = con.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT id FROM jobs WHERE state IN ('running','cancel_requested') AND lease_expires_at<clock_timestamp() ORDER BY lease_expires_at LIMIT 100"
            ).scalar_one()
            balances = list(con.execute(sa.select(m.quota_balances)).mappings())
        assert "jobs_claim_idx" in json.dumps(explain)
        assert "jobs_lease_idx" in json.dumps(reaper)
        assert all(b["reserved"] == 0 for b in balances)

        def summary(values):
            values = sorted(values)
            return dict(
                n=len(values),
                p50_ms=values[len(values) // 2],
                p95_ms=values[min(len(values) - 1, int(len(values) * 0.95))],
            )

        labels = {value: i for i, value in enumerate(dict.fromkeys(order))}
        report = dict(
            workers=workers,
            owners=5,
            jobs=14,
            global_cap=4,
            owner_cap=1,
            claim_latency=summary(records),
            heartbeat_latency=summary(beats),
            order=[labels[v] for v in order],
            first_five_distinct_owners=True,
            reservations_remaining=0,
            hardware=dict(
                system=platform.platform(),
                machine=platform.machine(),
                processor=platform.processor(),
            ),
            database="PostgreSQL 16.2 local Unix socket",
            production_capacity=False,
            explain=explain,
            reaper_explain=reaper,
            query_hash=content_hash(str(query)),
        )
        Path(f"artifacts/phase06/concurrency-{workers}.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )

    execute(uploads_database, tmp_path, scenario)


def test_actual_sigterm_checkpoints_and_requeues_without_failure_charge(uploads_database, tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    from test_worker_runtime import png

    from services.worker.runtime import VerificationWorker

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        await init_uploaded(uploads, owner, project, png())
        marker = tmp_path / "safe-checkpoint"
        script = tmp_path / "term_worker.py"
        script.write_text("""import asyncio,sys
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine
from app.adapters.storage.local_fs import LocalObjectStorage
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import UploadsService,QueueService
from app.modules.jobs.checkpoints import Checkpoints
from services.worker.runtime import VerificationWorker
save=Checkpoints.save
async def barrier(self,*args):
 result=await save(self,*args)
 Path(sys.argv[4]).write_text('checkpoint committed')
 await asyncio.sleep(.6)
 return result
Checkpoints.save=barrier
async def main():
 engine=create_async_engine(sys.argv[1],connect_args={'options':'-csearch_path='+sys.argv[2]})
 storage=LocalObjectStorage(Path(sys.argv[3]),secret=b's'*32)
 uploads=UploadsService(engine,IdentityService(engine),storage,derivative_max_bytes=1024)
 worker=VerificationWorker(QueueService(engine,uploads),uploads,storage,worker_id='term-target',heartbeat_seconds=.1,temp_root=Path(sys.argv[5]))
 await worker.run(mode='scheduled')
 await engine.dispose()
asyncio.run(main())
""")
        process = subprocess.Popen(
            [
                sys.executable,
                str(script),
                sync.url.render_as_string(hide_password=False),
                uploads_database[1],
                str(storage.root),
                str(marker),
                str(tmp_path / "attempts"),
            ],
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 10
            while not marker.exists():
                if process.poll() is not None:
                    raise AssertionError(process.stderr.read().decode())
                assert time.monotonic() < deadline
                await asyncio.sleep(0.02)
            process.send_signal(signal.SIGTERM)
            assert await asyncio.to_thread(process.wait, 5) == 0
            async with engine.connect() as con:
                job = (await con.execute(sa.select(m.jobs))).mappings().one()
            assert job["state"] == "queued" and job["attempts"] == 1 and job["max_attempts"] == 4
            assert await VerificationWorker(
                queue,
                uploads,
                storage,
                worker_id="term-recovery",
                heartbeat_seconds=0.1,
                temp_root=tmp_path / "attempts",
            ).run_once()
            async with engine.connect() as con:
                final = (await con.execute(sa.select(m.jobs))).mappings().one()
            assert final["state"] == "succeeded" and final["attempts"] == 2
            Path("artifacts/phase06/sigterm.json").write_text(
                __import__("json").dumps(
                    dict(
                        signal="SIGTERM",
                        exit_code=0,
                        checkpoint_retained=True,
                        attempts=2,
                        max_attempts=4,
                        state="succeeded",
                        failed_attempt_charge=0,
                    ),
                    indent=2,
                )
                + "\n"
            )
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, 5)
            process.stderr.close()

    execute(uploads_database, tmp_path, scenario)
