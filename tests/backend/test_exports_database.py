"""Real PostgreSQL export transactions, ownership, fencing and quota recovery."""

import asyncio
import hashlib
import uuid
from datetime import date, timedelta

import pytest
import sqlalchemy as sa
from app.core.errors import ApiError
from app.db import models as m
from app.modules.exports.schemas import ExportCreate
from app.modules.exports.service import ExportsService, encoded
from app.modules.glyphs.schemas import SnapshotCreate
from app.modules.glyphs.service import GlyphsService
from font_fixtures import fixture_glyphs
from penforge_contracts import content_hash
from sqlalchemy.exc import IntegrityError
from test_uploads_database import configure, create_project, execute, principal
from test_uploads_database import uploads_database as uploads_database

pytestmark = pytest.mark.integration


async def prepared(engine, identity, projects, storage, uploads, *, full=False):
    owner = principal()
    user, project = await create_project(identity, projects, owner)
    await configure(engine, user, amount=100_000_000)
    from sqlalchemy.dialects.postgresql import insert

    async with engine.begin() as con:
        for resource in ["exports", "compute_seconds"]:
            for scope in ["global", "user"]:
                await con.execute(
                    insert(m.quota_balances)
                    .values(
                        id=uuid.uuid4(),
                        scope=scope,
                        user_id=user if scope == "user" else None,
                        resource=resource,
                        window_kind="lifetime",
                        window_start=date(1970, 1, 1),
                        limit_amount=10000,
                    )
                    .on_conflict_do_nothing()
                )
        refs = {}
        for row in fixture_glyphs(full):
            gid, rid = uuid.uuid4(), uuid.uuid4()
            outline = row["outline"]
            await con.execute(
                m.glyphs.insert().values(
                    id=gid, project_id=project, codepoints=outline["codepoints"]
                )
            )
            await con.execute(
                m.glyph_revisions.insert().values(
                    id=rid,
                    glyph_id=gid,
                    seq=1,
                    origin="user_capture",
                    approval="approved",
                    outline_inline=outline,
                    metrics=outline["metrics"],
                    content_hash=bytes.fromhex(content_hash(outline)[7:]),
                    created_by_user_id=user,
                    approved_by_user_id=user,
                    approved_at=sa.func.now(),
                )
            )
            await con.execute(
                m.glyphs.update()
                .where(m.glyphs.c.id == gid)
                .values(active_revision_id=rid, coverage_status="approved")
            )
            refs[str(gid)] = str(rid)
    snapshot = await GlyphsService(engine, identity, storage).snapshot(
        owner, project, body=SnapshotCreate(label="Fixture"), idempotency_key=uuid.uuid4().hex
    )
    return owner, user, project, snapshot


async def create(service, owner, project, snapshot=None, formats=None, key=None):
    return await service.create(
        owner,
        project,
        key or uuid.uuid4().hex,
        ExportCreate(
            snapshot_id=snapshot["id"] if snapshot else None,
            formats=formats or ["ttf", "otf", "woff2"],
            family_name="Fixture Private",
        ),
    )


def test_atomic_create_idempotency_scope_and_reservations(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, snapshot = await prepared(
            engine, identity, projects, storage, uploads
        )
        service = ExportsService(engine, identity, uploads, storage)
        key = uuid.uuid4().hex
        a = await create(service, owner, project, snapshot, key=key)
        assert a == await create(service, owner, project, snapshot, key=key)
        with pytest.raises(ApiError) as conflict:
            await create(service, owner, project, snapshot, formats=["ttf"], key=key)
        assert conflict.value.code == "idempotency_key_reuse"
        other = principal()
        for operation in [
            service.get(other, uuid.UUID(a["export_ids"][0])),
            service.download(other, uuid.UUID(a["export_ids"][0])),
            create(service, other, project, snapshot),
        ]:
            with pytest.raises(ApiError) as denied:
                await operation
            assert denied.value.status == 404
        _, other_project = await create_project(identity, projects, owner)
        with pytest.raises(ApiError) as denied:
            await create(service, owner, other_project, snapshot)
        assert denied.value.status == 404
        async with engine.connect() as con:
            assert await con.scalar(sa.select(sa.func.count()).select_from(m.font_exports)) == 3
            assert (
                await con.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.jobs)
                    .where(m.jobs.c.job_type == "compile_export")
                )
                == 1
            )
            reservations = list((await con.execute(sa.select(m.quota_reservations))).mappings())
            assert all(r["status"] == "reserved" for r in reservations)
        assert storage.list_objects() == []
        job = await service.claim("one")
        assert job
        assert not await service.claim("two")
        assert len(job["_glyphs"]) == 52
        with pytest.raises(IntegrityError):
            async with engine.begin() as con:
                await con.execute(
                    m.font_exports.update()
                    .where(m.font_exports.c.id == uuid.UUID(a["export_ids"][0]))
                    .values(family_name="mutated")
                )
        assert await service.fail(job, "one", "invalid_input")
        async with engine.connect() as con:
            assert all(
                r["reserved"] == 0
                for r in (await con.execute(sa.select(m.quota_balances))).mappings()
            )
            assert set((await con.execute(sa.select(m.font_exports.c.status))).scalars()) == {
                "failed"
            }

    execute(uploads_database, tmp_path, scenario)


def test_stale_stage_reaper_terminal_cleanup_and_immutability(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, snapshot = await prepared(
            engine, identity, projects, storage, uploads
        )
        service = ExportsService(engine, identity, uploads, storage)
        result = await create(service, owner, project, snapshot, formats=["ttf"])
        job = await service.claim("old")
        aid = uuid.UUID(job["checkpoint"]["outputs"][result["export_ids"][0]]["font"])
        assert await service.stage(job, "old", {aid: b"crash-output"})
        async with engine.begin() as con:
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(lease_expires_at=sa.func.now() - timedelta(seconds=1))
            )
        assert not await service.stage(job, "old", {aid: b"stale"})
        assert not await service.publish(job, "old", {}, 0)
        await service.reap_expired()
        new = await service.claim("new")
        assert new["attempts"] == 2
        assert storage.list_objects() == []
        assert not await service.fail(job, "old")
        assert await service.fail(new, "new", "invalid_input")
        with pytest.raises(IntegrityError):
            async with engine.begin() as con:
                await con.execute(
                    m.font_exports.update()
                    .where(m.font_exports.c.id == uuid.UUID(result["export_ids"][0]))
                    .values(validation_status="passed")
                )
        async with engine.connect() as con:
            assert all(
                r["reserved"] == 0
                for r in (await con.execute(sa.select(m.quota_balances))).mappings()
            )

    execute(uploads_database, tmp_path, scenario)


def test_real_worker_all_formats_distinct_requests_download_and_immutable_bytes(
    uploads_database, tmp_path
):
    from services.worker.exports import ExportWorker

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, snapshot = await prepared(
            engine, identity, projects, storage, uploads
        )
        service = ExportsService(engine, identity, uploads, storage)
        worker = ExportWorker(
            service, worker_id="export", temp_root=tmp_path / "workers", heartbeat_seconds=0.05
        )
        first = await create(service, owner, project, snapshot)
        first_job = await service.claim("export")
        assert await worker.execute(first_job)
        hashes = []
        for eid in first["export_ids"]:
            value = await service.get(owner, uuid.UUID(eid))
            assert value["status"] == "ready", value
            assert value["validation_status"] == "passed_with_warnings"
            assert value["coverage_report"]["core_letters_mapped"] == 52
            url = await service.download(owner, uuid.UUID(eid))
            token = url["url"].rsplit("/", 1)[1]
            data, mime = await uploads.read_capability(token)
            assert value["sha256"] == "sha256:" + hashlib.sha256(data).hexdigest()
            hashes.append(value["sha256"])
            # Issuing a fresh capability revokes the old one; retry is authorized.
            renewed = await service.download(owner, uuid.UUID(eid))
            with pytest.raises(ApiError):
                await uploads.read_capability(token)
            assert renewed["url"] != url["url"]
        await asyncio.sleep(0.02)
        second = await create(service, owner, project, snapshot)
        assert set(first["export_ids"]).isdisjoint(second["export_ids"])
        assert await worker.run_once()
        assert hashes == [
            (await service.get(owner, uuid.UUID(eid)))["sha256"] for eid in second["export_ids"]
        ]
        assert not await service.publish(first_job, "export", {}, 0)
        async with engine.connect() as con:
            balances = list((await con.execute(sa.select(m.quota_balances))).mappings())
            assert all(row["reserved"] == 0 for row in balances)
        with pytest.raises(IntegrityError):
            async with engine.begin() as con:
                await con.execute(
                    m.font_exports.update()
                    .where(m.font_exports.c.id == uuid.UUID(first["export_ids"][0]))
                    .values(coverage_report={})
                )

    execute(uploads_database, tmp_path, scenario)


async def staged_real_build(service, worker, job):
    saved = {}
    publish = service.publish

    async def retain(job, holder, reports, usage):
        saved.update(reports=reports, usage=usage)
        return False

    service.publish = retain
    try:
        assert not await worker.execute(job)
    finally:
        service.publish = publish
    return saved


def test_concurrent_finalize_once_and_required_validation_report_cannot_be_forged(
    uploads_database, tmp_path
):
    from services.worker.exports import ExportWorker

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, snapshot = await prepared(
            engine, identity, projects, storage, uploads
        )
        service = ExportsService(engine, identity, uploads, storage)
        receipt = await create(service, owner, project, snapshot, formats=["ttf"])
        job = await service.claim("finalize")
        saved = await staged_real_build(
            service,
            ExportWorker(
                service, worker_id="finalize", temp_root=tmp_path / "worker", heartbeat_seconds=0.05
            ),
            job,
        )
        report = saved["reports"][receipt["export_ids"][0]]
        original = __import__("copy").deepcopy(report)
        report["layers"]["harfbuzz"]["status"] = "skipped"
        plans = job["checkpoint"]["outputs"][receipt["export_ids"][0]]
        aid = uuid.UUID(plans["report"])
        storage.delete_object(f"exports/{aid.hex}")
        assert await service.stage(job, "finalize", {aid: encoded(report)})
        with pytest.raises(ValueError, match="incomplete_validation"):
            await service.publish(job, "finalize", saved["reports"], saved["usage"])
        assert (await service.get(owner, uuid.UUID(receipt["export_ids"][0])))[
            "status"
        ] == "pending"
        saved["reports"][receipt["export_ids"][0]] = original
        storage.delete_object(f"exports/{aid.hex}")
        assert await service.stage(job, "finalize", {aid: encoded(original)})
        results = await asyncio.gather(
            service.publish(job, "finalize", saved["reports"], saved["usage"]),
            service.publish(job, "finalize", saved["reports"], saved["usage"]),
        )
        assert sorted(results) == [False, True]
        assert (
            len([obj for obj in storage.list_objects() if not obj.key.startswith("checkpoints/")])
            == 2
        )

    execute(uploads_database, tmp_path, scenario)


def test_expiry_during_publish_rolls_back_assets_rows_and_quota(uploads_database, tmp_path):
    from services.worker.exports import ExportWorker

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, snapshot = await prepared(
            engine, identity, projects, storage, uploads
        )
        service = ExportsService(engine, identity, uploads, storage)
        receipt = await create(service, owner, project, snapshot, formats=["ttf"])
        job = await service.claim("fenced")
        saved = await staged_real_build(
            service,
            ExportWorker(
                service, worker_id="fenced", temp_root=tmp_path / "worker", heartbeat_seconds=0.05
            ),
            job,
        )
        original = uploads.publish_worker_artifact

        async def slow(*args, **kwargs):
            await original(*args, **kwargs)
            await asyncio.sleep(0.15)

        uploads.publish_worker_artifact = slow
        async with engine.begin() as con:
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(lease_expires_at=sa.func.clock_timestamp() + timedelta(seconds=0.1))
            )
        with pytest.raises(ApiError) as failed:
            await service.publish(job, "fenced", saved["reports"], saved["usage"])
        assert failed.value.status == 409
        assert (await service.get(owner, uuid.UUID(receipt["export_ids"][0])))[
            "status"
        ] == "pending"
        async with engine.connect() as con:
            assert set(
                (
                    await con.execute(
                        sa.select(m.assets.c.state).where(m.assets.c.kind != "checkpoint")
                    )
                ).scalars()
            ) == {"pending_upload"}
            retained_bytes = await con.scalar(
                sa.select(sa.func.coalesce(sa.func.sum(m.assets.c.size_bytes), 0)).where(
                    m.assets.c.kind == "checkpoint", m.assets.c.state == "ready"
                )
            )
            assert all(
                row["committed"] == (retained_bytes if row["resource"] == "storage_bytes" else 0)
                for row in (await con.execute(sa.select(m.quota_balances))).mappings()
            )
        uploads.publish_worker_artifact = original
        await service.reap_expired()
        retry = await service.claim("retry")
        assert all(obj.key.startswith("checkpoints/") for obj in storage.list_objects())
        assert await service.fail(retry, "retry", "invalid_input")

    execute(uploads_database, tmp_path, scenario)


def test_quota_failure_rolls_back_snapshot_export_job_assets_and_receipt(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, snapshot = await prepared(
            engine, identity, projects, storage, uploads
        )
        service = ExportsService(engine, identity, uploads, storage)
        async with engine.begin() as con:
            await con.execute(
                m.quota_balances.update()
                .where(m.quota_balances.c.resource == "compute_seconds")
                .values(limit_amount=0)
            )
        with pytest.raises(ApiError) as failed:
            await create(service, owner, project)
        assert failed.value.code == "quota_exceeded"
        async with engine.connect() as con:
            for table in [m.font_exports, m.jobs, m.assets, m.quota_reservations]:
                assert await con.scalar(sa.select(sa.func.count()).select_from(table)) == 0
            assert (
                await con.scalar(sa.select(sa.func.count()).select_from(m.project_snapshots)) == 1
            )
            assert all(
                row["reserved"] == 0
                for row in (await con.execute(sa.select(m.quota_balances))).mappings()
            )

    execute(uploads_database, tmp_path, scenario)


def test_sigkill_after_object_stage_recovers_without_orphans_or_reservation_leak(
    uploads_database, tmp_path
):
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    from services.worker.exports import ExportWorker

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, snapshot = await prepared(
            engine, identity, projects, storage, uploads
        )
        service = ExportsService(engine, identity, uploads, storage)
        receipt = await create(service, owner, project, snapshot, formats=["ttf"])
        marker = tmp_path / "staged-marker"
        source = tmp_path / "crash.py"
        source.write_text("""import asyncio,hashlib,time
from pathlib import Path
from uuid import UUID
from sqlalchemy.ext.asyncio import create_async_engine
from app.adapters.storage.local_fs import LocalObjectStorage
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import UploadsService
from app.modules.exports.service import ExportsService, encoded
async def main():
 db=create_async_engine(URL,connect_args={"options":"-csearch_path="+SCHEMA})
 storage=LocalObjectStorage(Path(OBJECTS),secret=b"s"*32)
 identity=IdentityService(db);uploads=UploadsService(db,identity,storage)
 service=ExportsService(db,identity,uploads,storage)
 job=await service.claim("killed")
 aid=UUID(next(iter(job["checkpoint"]["outputs"].values()))["font"])
 assert await service.stage(job,"killed",{aid:b"partial-attempt-before-real-compile"})
 Path(MARKER).write_text(str(job["id"]))
 await asyncio.sleep(60)
asyncio.run(main())
""")
        prefix = f"URL={sync.url.render_as_string(hide_password=False)!r}\nSCHEMA={uploads_database[1]!r}\nOBJECTS={str(tmp_path / 'objects')!r}\nMARKER={str(marker)!r}\n"
        source.write_text(prefix + source.read_text())
        process = subprocess.Popen(
            [sys.executable, str(source)],
            cwd=Path(__file__).resolve().parents[2],
            env=os.environ.copy(),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.02)
            assert marker.exists()
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            assert len(storage.list_objects()) == 1
            async with engine.begin() as con:
                await con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == uuid.UUID(receipt["job_id"]))
                    .values(lease_expires_at=sa.func.now() - timedelta(seconds=1))
                )
            assert await ExportWorker(
                service,
                worker_id="recovered",
                temp_root=tmp_path / "worker",
                heartbeat_seconds=0.05,
            ).run_once()
            assert (await service.get(owner, uuid.UUID(receipt["export_ids"][0])))[
                "status"
            ] == "ready"
            assert (
                len(
                    [
                        obj
                        for obj in storage.list_objects()
                        if not obj.key.startswith("checkpoints/")
                    ]
                )
                == 2
            )
            assert (await uploads.reconcile())["orphan_keys"] == []
            async with engine.connect() as con:
                assert all(
                    row["reserved"] == 0
                    for row in (await con.execute(sa.select(m.quota_balances))).mappings()
                )
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    execute(uploads_database, tmp_path, scenario)


@pytest.mark.parametrize("blocked", ["deleting", "suspended", "expired_output"])
def test_queued_unavailable_scope_or_output_is_terminal_and_releases_capacity(
    uploads_database, tmp_path, blocked
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, snapshot = await prepared(
            engine, identity, projects, storage, uploads
        )
        service = ExportsService(engine, identity, uploads, storage)
        await create(service, owner, project, snapshot, formats=["ttf"])
        async with engine.begin() as con:
            if blocked == "deleting":
                await con.execute(
                    m.projects.update().where(m.projects.c.id == project).values(status="deleting")
                )
            elif blocked == "suspended":
                await con.execute(
                    m.users.update().where(m.users.c.id == user).values(account_state="suspended")
                )
            else:
                await con.execute(
                    m.assets.update()
                    .where(m.assets.c.project_id == project)
                    .values(expires_at=sa.func.now() - timedelta(seconds=1))
                )
        assert await service.claim("blocked") is None
        async with engine.connect() as con:
            assert set((await con.execute(sa.select(m.font_exports.c.status))).scalars()) == {
                "failed"
            }
            assert all(
                row["reserved"] == 0
                for row in (await con.execute(sa.select(m.quota_balances))).mappings()
            )
        assert storage.list_objects() == []

    execute(uploads_database, tmp_path, scenario)
