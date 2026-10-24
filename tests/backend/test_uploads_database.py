"""Real PostgreSQL capture quota, claim, publication and lease races."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.adapters.auth.base import Principal
from app.adapters.storage.local_fs import LocalObjectStorage
from app.core.errors import ApiError
from app.db import models as m
from app.db.session import normalize_database_url
from app.modules.identity.service import IdentityService
from app.modules.projects.service import ProjectsService
from app.modules.uploads.schemas import (
    AssetSampleCreate,
    StrokeSampleCreate,
    TemplateReference,
    UploadInit,
)
from app.modules.uploads.service import QueueService, UploadsService, VerifiedAsset
from penforge_contracts import StrokeDocument, TemplateManifest, content_hash
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
SOURCE = b"synthetic source bytes; decoder independently tested"
THUMB = b"synthetic bounded derivative"


@pytest.fixture()
def uploads_database() -> Iterator[tuple[Engine, str]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL required for PostgreSQL upload tests")
    admin = sa.create_engine(normalize_database_url(url))
    schema = "phase03_upload_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(admin.url, connect_args={"options": f"-csearch_path={schema}"})
    with engine.begin() as connection:
        config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    try:
        yield engine, schema
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def principal(anonymous: bool = False) -> Principal:
    now = int(time.time())
    return Principal(
        user_id=uuid.uuid4(),
        firebase_uid=uuid.uuid4().hex,
        is_anonymous=anonymous,
        sign_in_provider="anonymous" if anonymous else "password",
        email_verified=not anonymous,
        auth_time=now,
        token_issued_at=now,
    )


def execute(
    database: tuple[Engine, str],
    tmp_path: Path,
    scenario: Callable[..., Awaitable[None]],
    *,
    storage_class: type[LocalObjectStorage] = LocalObjectStorage,
    lease: int = 60,
) -> None:
    sync, schema = database

    async def run() -> None:
        engine = create_async_engine(
            sync.url,
            connect_args={"options": f"-csearch_path={schema}"},
            pool_size=20,
            max_overflow=20,
        )
        identity = IdentityService(engine)
        projects = ProjectsService(engine, identity, cursor_secret=b"p" * 32)
        storage = storage_class(tmp_path / "objects", secret=b"s" * 32)
        uploads = UploadsService(engine, identity, storage, derivative_max_bytes=1024)
        queue = QueueService(engine, uploads, lease_seconds=lease, retry_backoff_seconds=0)
        try:
            await scenario(sync, engine, identity, projects, storage, uploads, queue)
        finally:
            storage.close()
            await engine.dispose()

    asyncio.run(run())


async def create_project(
    identity: IdentityService, projects: ProjectsService, owner: Principal, *, name: str = "Capture"
) -> tuple[uuid.UUID, uuid.UUID]:
    user = await identity.me(owner)
    _, _, project = await projects.create(
        owner, idempotency_key=uuid.uuid4().hex, name=name, script_code="Latn", style_controls={}
    )
    return uuid.UUID(str(user["user_id"])), uuid.UUID(project["id"])


async def configure(
    engine: AsyncEngine,
    owner: uuid.UUID,
    amount: int = 10_000_000,
    *,
    global_amount: int = 1_000_000_000,
) -> None:
    async with engine.begin() as connection:
        global_id = await connection.scalar(
            sa.select(m.quota_balances.c.id).where(
                m.quota_balances.c.scope == "global", m.quota_balances.c.resource == "storage_bytes"
            )
        )
        if global_id is None:
            await connection.execute(
                m.quota_balances.insert().values(
                    id=uuid.uuid4(),
                    scope="global",
                    resource="storage_bytes",
                    window_kind="lifetime",
                    window_start=date(1970, 1, 1),
                    limit_amount=global_amount,
                )
            )
        await connection.execute(
            m.quota_balances.insert().values(
                id=uuid.uuid4(),
                scope="user",
                user_id=owner,
                resource="storage_bytes",
                window_kind="lifetime",
                window_start=date(1970, 1, 1),
                limit_amount=amount,
            )
        )

        from sqlalchemy.dialects.postgresql import insert

        await connection.execute(
            insert(m.system_settings)
            .values(key="queue_policy.v1", value={"version": 1, "retry_base": 0})
            .on_conflict_do_nothing()
        )
        for scope in ("global", "user"):
            await connection.execute(
                insert(m.quota_balances)
                .values(
                    id=uuid.uuid4(),
                    scope=scope,
                    user_id=owner if scope == "user" else None,
                    resource="compute_seconds",
                    window_kind="lifetime",
                    window_start=date(1970, 1, 1),
                    limit_amount=10000,
                )
                .on_conflict_do_nothing()
            )


def body(data: bytes = SOURCE) -> UploadInit:
    return UploadInit(
        filename="private writing.png",
        declared_mime="image/png",
        declared_size=len(data),
        source_type="freeform_photo",
        sha256="sha256:" + hashlib.sha256(data).hexdigest(),
    )


async def init_uploaded(
    uploads: UploadsService, owner: Principal, project: uuid.UUID, data: bytes = SOURCE
) -> tuple[uuid.UUID, dict[str, Any]]:
    _, created = await uploads.initialize(
        owner, project, idempotency_key=uuid.uuid4().hex, body=body(data)
    )
    await uploads.put_capability(created["upload"]["url"].rsplit("/", 1)[1], data)
    asset_id = uuid.UUID(created["asset_id"])
    return asset_id, await uploads.complete(owner, asset_id)


def result(
    storage: LocalObjectStorage, queue: QueueService, job: dict[str, Any], data: bytes = SOURCE
) -> VerifiedAsset:
    key = queue.derivative_key(job)
    storage.put_immutable(key, THUMB)
    return VerifiedAsset(
        hashlib.sha256(data).hexdigest(),
        len(data),
        "image/png",
        100,
        50,
        key,
        len(THUMB),
        hashlib.sha256(THUMB).hexdigest(),
        100,
        50,
    )


async def publish(
    queue: QueueService, storage: LocalObjectStorage, job: dict[str, Any], data: bytes = SOURCE
):
    return await queue.publish_verified(
        job["id"],
        job["lease_holder"],
        job["attempts"],
        job["owner_user_id"],
        result(storage, queue, job, data),
    )


def test_atomic_init_and_complete_replay_one_resource_job_and_reservation(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user, amount=len(SOURCE) + 1024)
        key = uuid.uuid4().hex
        attempts = await asyncio.gather(
            *(
                uploads.initialize(owner, project, idempotency_key=key, body=body())
                for _ in range(8)
            )
        )
        assert sum(not replay for replay, _ in attempts) == 1
        assert all(value == attempts[0][1] for _, value in attempts)
        created = attempts[0][1]
        with pytest.raises(ApiError) as changed:
            await uploads.initialize(owner, project, idempotency_key=key, body=body(b"changed"))
        assert changed.value.code == "idempotency_key_reuse"
        await uploads.put_capability(created["upload"]["url"].rsplit("/", 1)[1], SOURCE)
        aid = uuid.UUID(created["asset_id"])
        completes = await asyncio.gather(*(uploads.complete(owner, aid) for _ in range(8)))
        assert len({r["job_id"] for r in completes}) == 1
        with sync.connect() as connection:
            assert (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.jobs)
                    .where(m.jobs.c.owner_user_id == user)
                )
                == 1
            )
            assert (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.quota_reservations)
                    .where(m.quota_reservations.c.asset_id == aid)
                )
                == 2
            )
        with pytest.raises(ApiError) as exceeded:
            await uploads.initialize(owner, project, idempotency_key=uuid.uuid4().hex, body=body())
        assert exceeded.value.code == "quota_exceeded"
        claims = await asyncio.gather(queue.claim("a"), queue.claim("b"))
        assert sum(item is not None for item in claims) == 1
        job = next(item for item in claims if item is not None)
        assert (await publish(queue, storage, job)).state == "ready"
        with sync.connect() as connection:
            balance = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
            assert balance["reserved"] == 0 and balance["committed"] == len(SOURCE) + len(THUMB)
        cap = await uploads.download(owner, aid)
        assert (await uploads.read_capability(cap["url"].rsplit("/", 1)[1])) == (
            SOURCE,
            "image/png",
        )

    execute(uploads_database, tmp_path, scenario)


def test_unconfigured_quota_foreign_scope_and_missing_objects_fail_closed(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, foreign = principal(), principal()
        user, project = await create_project(identity, projects, owner)
        with pytest.raises(ApiError) as unknown:
            await uploads.initialize(owner, project, idempotency_key=uuid.uuid4().hex, body=body())
        assert unknown.value.code == "quota_unconfigured"
        with pytest.raises(ApiError) as hidden:
            await uploads.initialize(
                foreign, project, idempotency_key=uuid.uuid4().hex, body=body()
            )
        assert hidden.value.status == 404
        await configure(engine, user)
        _, created = await uploads.initialize(
            owner, project, idempotency_key=uuid.uuid4().hex, body=body()
        )
        aid = uuid.UUID(created["asset_id"])
        for action in (uploads.get, uploads.complete, uploads.download):
            with pytest.raises(ApiError) as hidden:
                await action(foreign, aid)
            assert hidden.value.status == 404
        with pytest.raises(ApiError) as missing:
            await uploads.complete(owner, aid)
        assert missing.value.code == "object_missing"
        token = created["upload"]["url"].rsplit("/", 1)[1]
        with pytest.raises(ApiError):
            await uploads.read_capability(token)
        with pytest.raises(ApiError) as wrong:
            await uploads.put_capability(token, b"wrong")
        assert wrong.value.code == "upload_mismatch"

    execute(uploads_database, tmp_path, scenario)


def test_parallel_quota_requests_never_overrun_current_user_and_global(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        amount = len(SOURCE) + 1024
        await configure(engine, user, amount=amount * 3)
        outcomes = await asyncio.gather(
            *(
                uploads.initialize(owner, project, idempotency_key=uuid.uuid4().hex, body=body())
                for _ in range(12)
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(r, Exception) for r in outcomes) == 3
        assert all(
            isinstance(r, ApiError) and r.code == "quota_exceeded"
            for r in outcomes
            if isinstance(r, Exception)
        )
        with sync.connect() as connection:
            row = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
            assert row["reserved"] == row["limit_amount"] == amount * 3

    execute(uploads_database, tmp_path, scenario)


def test_expired_lease_and_wrong_holder_attempt_owner_cannot_publish_or_heartbeat(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        aid, complete = await init_uploaded(uploads, owner, project)
        job = await queue.claim("worker-a")
        assert job is not None
        verified = result(storage, queue, job)
        for holder, attempt, owner_id in (
            ("other", job["attempts"], user),
            ("worker-a", job["attempts"] + 1, user),
            ("worker-a", job["attempts"], uuid.uuid4()),
        ):
            assert not (
                await queue.publish_verified(job["id"], holder, attempt, owner_id, verified)
            ).accepted
        async with engine.begin() as connection:
            await connection.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert not await queue.heartbeat(
            job["id"], "worker-a", job["attempts"], user, stage="decode", completed_units=1
        )
        assert not (
            await queue.publish_verified(job["id"], "worker-a", job["attempts"], user, verified)
        ).accepted
        assert await queue.reap_expired() == 1
        next_job = await queue.claim("worker-b")
        assert next_job["attempts"] == 2
        assert (await publish(queue, storage, next_job)).state == "ready"
        assert not (
            await queue.publish_verified(job["id"], "worker-a", job["attempts"], user, verified)
        ).accepted
        assert (await uploads.get_job(owner, uuid.UUID(complete["job_id"])))["eta_seconds"] is None

    execute(uploads_database, tmp_path, scenario)


def test_claim_revokes_old_capability_and_stale_worker_then_retry_uses_new_owner(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        guest, target = principal(True), principal()
        old_user, project = await create_project(identity, projects, guest)
        await configure(engine, old_user)
        _, created = await uploads.initialize(
            guest, project, idempotency_key=uuid.uuid4().hex, body=body()
        )
        token = created["upload"]["url"].rsplit("/", 1)[1]
        aid = uuid.UUID(created["asset_id"])
        await identity.claim(target, guest)
        with pytest.raises(ApiError) as revoked:
            await uploads.put_capability(token, SOURCE)
        assert revoked.value.status == 404
        renewed = await uploads.renew_upload(target, aid)
        await uploads.put_capability(renewed["upload"]["url"].rsplit("/", 1)[1], SOURCE)
        await uploads.complete(target, aid)
        job = await queue.claim("new-owner-worker")
        assert job is not None and job["owner_user_id"] != old_user
        assert (await publish(queue, storage, job)).state == "ready"
        assert f"u/{old_user}/" in job["_asset"]["storage_key"]

        guest2, target2 = principal(True), principal()
        old_user2, project2 = await create_project(identity, projects, guest2)
        await configure(engine, old_user2)
        aid2, _ = await init_uploaded(uploads, guest2, project2, b"another source")
        job2 = await queue.claim("old-worker")
        verified = result(storage, queue, job2, b"another source")
        await identity.claim(target2, guest2)
        assert not (
            await queue.publish_verified(
                job2["id"], "old-worker", job2["attempts"], old_user2, verified
            )
        ).accepted
        with sync.begin() as connection:
            connection.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job2["id"])
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert await queue.reap_expired() == 1
        retried = await queue.claim("claimed-worker")
        assert retried["owner_user_id"] != old_user2
        assert (await publish(queue, storage, retried, b"another source")).accepted
        assert (await uploads.get(target2, aid2))["state"] == "ready"

    execute(uploads_database, tmp_path, scenario)


def test_simultaneous_owner_local_duplicates_cross_project_samples_and_cleanup_once(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project_a = await create_project(identity, projects, owner)
        _, project_b = await create_project(identity, projects, owner, name="Second")
        await configure(engine, user)
        await asyncio.gather(
            init_uploaded(uploads, owner, project_a), init_uploaded(uploads, owner, project_b)
        )
        jobs = await asyncio.gather(queue.claim("dedupe-a"), queue.claim("dedupe-b"))
        assert all(jobs)
        verified = [result(storage, queue, j) for j in jobs]
        published = await asyncio.gather(
            *(
                queue.publish_verified(j["id"], j["lease_holder"], j["attempts"], user, r)
                for j, r in zip(jobs, verified, strict=True)
            )
        )
        assert sorted(r.state for r in published) == ["ready", "rejected"]
        duplicate = next(j for j, r in zip(jobs, published, strict=True) if r.state == "rejected")
        rejected = await uploads.get(owner, duplicate["_asset"]["id"])
        assert rejected["duplicate_of"]
        _, sample = await uploads.register_sample(
            owner,
            duplicate["project_id"],
            idempotency_key=uuid.uuid4().hex,
            body=AssetSampleCreate(
                source_kind="asset",
                asset_id=duplicate["_asset"]["id"],
                source_type="freeform_photo",
            ),
        )
        assert sample["asset_id"] == str(duplicate["_asset"]["id"])
        # Remove staged duplicate thumbnail, then durable cleanup releases exactly once.
        storage.delete_object(queue.derivative_key(duplicate))
        cleaned = await asyncio.gather(
            *(uploads.cleanup_asset(duplicate["_asset"]["id"]) for _ in range(4))
        )
        assert sum(cleaned) == 1
        cap = await uploads.download(owner, duplicate["_asset"]["id"])
        assert (await uploads.read_capability(cap["url"].rsplit("/", 1)[1]))[0] == SOURCE
        other = principal()
        user2, project2 = await create_project(identity, projects, other)
        await configure(engine, user2)
        await init_uploaded(uploads, other, project2)
        job = await queue.claim("other-owner")
        assert (await publish(queue, storage, job)).state == "ready"
        with sync.connect() as connection:
            balance = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
            assert balance["committed"] == len(SOURCE) + len(THUMB) and balance["reserved"] == 0

    execute(uploads_database, tmp_path, scenario)


def test_abandoned_upload_reaper_and_reconciliation_do_not_delete_orphans(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        _, created = await uploads.initialize(
            owner, project, idempotency_key=uuid.uuid4().hex, body=body()
        )
        aid = uuid.UUID(created["asset_id"])
        await uploads.put_capability(created["upload"]["url"].rsplit("/", 1)[1], SOURCE)
        with sync.begin() as connection:
            connection.execute(
                m.assets.update()
                .where(m.assets.c.id == aid)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        outcomes = await asyncio.gather(uploads.reap_abandoned(), uploads.reap_abandoned())
        assert sum(outcomes) == 1
        assert await uploads.reap_abandoned() == 0
        assert (await uploads.get(owner, aid))["state"] == "expired"
        storage.put_immutable("orphans/unknown", b"unreferenced")
        report = await uploads.reconcile()
        assert report["dry_run"] and "orphans/unknown" in report["orphan_keys"]
        assert storage.get_metadata("orphans/unknown").size_bytes == 12
        with sync.connect() as connection:
            row = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
            assert row["reserved"] == row["committed"] == 0

    execute(uploads_database, tmp_path, scenario)


def test_raw_strokes_preserve_legacy_missing_metadata_and_derived_source_lineage(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, other = principal(), principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        raw = json.loads((ROOT / "packages/contracts/fixtures/strokes.v1.json").read_text())
        document = StrokeDocument.model_validate(raw)
        key = uuid.uuid4().hex
        request = StrokeSampleCreate(source_kind="strokes", stroke_document=document)
        _, sample = await uploads.register_sample(owner, project, idempotency_key=key, body=request)
        replayed, replay = await uploads.register_sample(
            owner, project, idempotency_key=key, body=request
        )
        assert replayed and replay == sample
        asset_id = uuid.UUID(sample["asset_id"])
        cap = await uploads.download(owner, asset_id)
        original, _ = await uploads.read_capability(cap["url"].rsplit("/", 1)[1])
        assert json.loads(original) == document.model_dump(mode="json")
        derived = document.model_dump(mode="json")
        derived["parent_source_hash"] = sample["document_hash"]
        derived["operations"] = [
            {
                "type": "translate",
                "stroke_ids": [derived["strokes"][0]["stroke_id"]],
                "dx": 0.1,
                "dy": 0.0,
            }
        ]
        _, edited = await uploads.register_sample(
            owner,
            project,
            idempotency_key=uuid.uuid4().hex,
            body=StrokeSampleCreate(
                source_kind="strokes",
                stroke_document=StrokeDocument.model_validate(derived),
                parent_asset_id=asset_id,
            ),
        )
        assert edited["document_hash"] != sample["document_hash"]
        assert (await uploads.read_capability(cap["url"].rsplit("/", 1)[1]))[0] == original
        legacy = json.loads(
            (ROOT / "packages/contracts/fixtures/legacy-strokes.v1.json").read_text()
        )
        legacy_document = StrokeDocument.model_validate(legacy)
        _, old = await uploads.register_sample(
            owner,
            project,
            idempotency_key=uuid.uuid4().hex,
            body=StrokeSampleCreate(source_kind="strokes", stroke_document=legacy_document),
        )
        old_cap = await uploads.download(owner, uuid.UUID(old["asset_id"]))
        restored = json.loads((await uploads.read_capability(old_cap["url"].rsplit("/", 1)[1]))[0])
        assert restored["timing"] == "unavailable_legacy" and restored["capture_size_px"] is None
        assert restored["strokes"][0]["points"][0]["pressure"] is None
        with pytest.raises(ApiError) as hidden:
            await uploads.register_sample(
                other, project, idempotency_key=uuid.uuid4().hex, body=request
            )
        assert hidden.value.status == 404

    execute(uploads_database, tmp_path, scenario)


def test_lease_expiry_during_storage_validation_rolls_back_publication(uploads_database, tmp_path):
    class SlowStorage(LocalObjectStorage):
        slow = False

        def verify_upload(self, *args, **kwargs):
            if self.slow:
                time.sleep(0.7)
            return super().verify_upload(*args, **kwargs)

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        aid, _ = await init_uploaded(uploads, owner, project, b"slow source")
        job = await queue.claim("slow-worker")
        verified = result(storage, queue, job, b"slow source")
        storage.slow = True
        assert not (
            await queue.publish_verified(job["id"], "slow-worker", 1, user, verified)
        ).accepted
        assert (await uploads.get(owner, aid))["state"] == "verifying"
        with sync.connect() as connection:
            assert (
                connection.scalar(
                    sa.select(m.quota_balances.c.committed).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                == 0
            )

    execute(uploads_database, tmp_path, scenario, storage_class=SlowStorage, lease=1)


def test_claim_overlaps_put_and_publication_without_changing_historical_bytes(
    uploads_database, tmp_path
):
    class BarrierStorage(LocalObjectStorage):
        entered = threading.Event()
        release = threading.Event()
        block_put = False
        block_verify = False

        def put(self, *args, **kwargs):
            if self.block_put:
                self.entered.set()
                assert self.release.wait(5)
            return super().put(*args, **kwargs)

        def verify_upload(self, *args, **kwargs):
            if self.block_verify:
                self.entered.set()
                assert self.release.wait(5)
            return super().verify_upload(*args, **kwargs)

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        guest, target = principal(True), principal()
        old_user, project = await create_project(identity, projects, guest)
        await configure(engine, old_user)
        _, created = await uploads.initialize(
            guest, project, idempotency_key=uuid.uuid4().hex, body=body()
        )
        aid = uuid.UUID(created["asset_id"])
        token = created["upload"]["url"].rsplit("/", 1)[1]
        storage.block_put = True
        write = asyncio.create_task(uploads.put_capability(token, SOURCE))
        assert await asyncio.to_thread(storage.entered.wait, 3)
        claim = asyncio.create_task(identity.claim(target, guest))
        await asyncio.sleep(0.05)
        assert not claim.done(), (
            "claim must wait while upload authorization and storage commit share transaction"
        )
        storage.release.set()
        await asyncio.gather(write, claim)
        storage.block_put = False
        with pytest.raises(ApiError):
            await uploads.put_capability(token, SOURCE)
        await uploads.complete(target, aid)
        job = await queue.claim("overlap-worker")
        assert (await publish(queue, storage, job)).accepted
        cap = await uploads.download(target, aid)
        assert (await uploads.read_capability(cap["url"].rsplit("/", 1)[1]))[0] == SOURCE

        guest2, target2 = principal(True), principal()
        user2, project2 = await create_project(identity, projects, guest2)
        await configure(engine, user2)
        aid2, _ = await init_uploaded(uploads, guest2, project2, b"publication overlap")
        job2 = await queue.claim("publication-overlap")
        verified = result(storage, queue, job2, b"publication overlap")
        storage.entered.clear()
        storage.release.clear()
        storage.block_verify = True
        publication = asyncio.create_task(
            queue.publish_verified(
                job2["id"], job2["lease_holder"], job2["attempts"], user2, verified
            )
        )
        assert await asyncio.to_thread(storage.entered.wait, 3)
        claim2 = asyncio.create_task(identity.claim(target2, guest2))
        await asyncio.sleep(0.05)
        assert not claim2.done()
        storage.release.set()
        published, claimed = await asyncio.gather(publication, claim2)
        assert published.accepted and claimed["transferred"]["assets"] == 2
        assert (await uploads.get(target2, aid2))["state"] == "ready"
        assert not await queue.heartbeat(
            job2["id"],
            job2["lease_holder"],
            job2["attempts"],
            user2,
            stage="publish",
            completed_units=3,
        )

    execute(uploads_database, tmp_path, scenario, storage_class=BarrierStorage)


@pytest.mark.parametrize("blocked", ["suspended", "deleting", "project_deleting"])
def test_current_account_and_project_state_block_worker_publication(
    uploads_database, tmp_path, blocked
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        aid, _ = await init_uploaded(uploads, owner, project, blocked.encode())
        job = await queue.claim("blocked-worker")
        verified = result(storage, queue, job, blocked.encode())
        async with engine.begin() as connection:
            if blocked == "project_deleting":
                await connection.execute(
                    m.projects.update().where(m.projects.c.id == project).values(status="deleting")
                )
            else:
                await connection.execute(
                    m.users.update().where(m.users.c.id == user).values(account_state=blocked)
                )
        assert not (
            await queue.publish_verified(
                job["id"], job["lease_holder"], job["attempts"], user, verified
            )
        ).accepted
        with sync.connect() as connection:
            assert (
                connection.scalar(sa.select(m.assets.c.state).where(m.assets.c.id == aid))
                == "verifying"
            )
            assert (
                connection.scalar(
                    sa.select(m.quota_balances.c.committed).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                == 0
            )

    execute(uploads_database, tmp_path, scenario)


def test_claim_aliases_keep_ready_bytes_and_dedupe_under_destination_owner(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        guest, target = principal(True), principal()
        gu, gp = await create_project(identity, projects, guest)
        tu, tp = await create_project(identity, projects, target)
        await configure(engine, gu)
        await configure(engine, tu)
        target_asset, _ = await init_uploaded(uploads, target, tp)
        first = await queue.claim("target-ready")
        assert (await publish(queue, storage, first)).state == "ready"
        guest_asset, _ = await init_uploaded(uploads, guest, gp)
        second = await queue.claim("guest-ready")
        assert (await publish(queue, storage, second)).state == "ready"
        await identity.claim(target, guest)
        preserved = await uploads.get(target, guest_asset)
        assert preserved["state"] == "ready" and preserved["duplicate_of"] == str(target_asset)
        with sync.connect() as connection:
            historical = connection.scalar(
                sa.select(m.assets.c.storage_key).where(m.assets.c.id == guest_asset)
            )
        assert storage.read_object(historical, max_bytes=1000) == SOURCE
        new_asset, _ = await init_uploaded(uploads, target, gp)
        third = await queue.claim("claimed-dedupe")
        duplicate = await publish(queue, storage, third)
        assert duplicate.state == "rejected" and duplicate.duplicate_of == target_asset
        _, sample = await uploads.register_sample(
            target,
            gp,
            idempotency_key=uuid.uuid4().hex,
            body=AssetSampleCreate(
                source_kind="asset", asset_id=new_asset, source_type="freeform_photo"
            ),
        )
        assert sample["source_sha256"] == "sha256:" + hashlib.sha256(SOURCE).hexdigest()
        assert await uploads.cleanup_asset(new_asset)
        assert storage.read_object(historical, max_bytes=1000) == SOURCE

    execute(uploads_database, tmp_path, scenario)


def test_template_digest_identity_and_sample_binding_are_checked_again_on_publish(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        manifest = TemplateManifest.model_validate_json(
            (ROOT / "packages/contracts/fixtures/template.v1.json").read_text()
        )
        digest = content_hash(manifest.model_dump(mode="json"))
        key = (manifest.template_id, manifest.template_version, manifest.page.index)
        uploads.templates[key] = (manifest, digest)
        ref = TemplateReference(
            template_id=manifest.template_id,
            template_version=manifest.template_version,
            manifest_sha256=digest,
            page_index=manifest.page.index,
        )
        request = body().model_copy(update={"source_type": "template_scan", "template": ref})
        wrong = request.model_copy(
            update={"template": ref.model_copy(update={"manifest_sha256": "sha256:" + "0" * 64})}
        )
        with pytest.raises(ApiError) as mismatch:
            await uploads.initialize(owner, project, idempotency_key=uuid.uuid4().hex, body=wrong)
        assert mismatch.value.code == "template_mismatch"
        _, created = await uploads.initialize(
            owner, project, idempotency_key=uuid.uuid4().hex, body=request
        )
        aid = uuid.UUID(created["asset_id"])
        await uploads.put_capability(created["upload"]["url"].rsplit("/", 1)[1], SOURCE)
        await uploads.complete(owner, aid)
        job = await queue.claim("template-worker")
        verified = result(storage, queue, job)
        uploads.templates.pop(key)
        with pytest.raises(ApiError) as archived_missing:
            await queue.publish_verified(
                job["id"], job["lease_holder"], job["attempts"], user, verified
            )
        assert archived_missing.value.code == "template_mismatch"
        uploads.templates[key] = (manifest, digest)
        assert (
            await queue.publish_verified(
                job["id"], job["lease_holder"], job["attempts"], user, verified
            )
        ).accepted
        with pytest.raises(ApiError) as relabeled:
            await uploads.register_sample(
                owner,
                project,
                idempotency_key=uuid.uuid4().hex,
                body=AssetSampleCreate(
                    source_kind="asset", asset_id=aid, source_type="freeform_photo"
                ),
            )
        assert relabeled.value.code == "template_mismatch"
        _, sample = await uploads.register_sample(
            owner,
            project,
            idempotency_key=uuid.uuid4().hex,
            body=AssetSampleCreate(
                source_kind="asset", asset_id=aid, source_type="template_scan", template=ref
            ),
        )
        assert sample["template"]["manifest_sha256"] == digest

    execute(uploads_database, tmp_path, scenario)


def test_max_attempt_expiry_cleans_known_staged_outputs_and_releases_quota(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        aid, _ = await init_uploaded(uploads, owner, project, b"crash sequence")
        for attempt in range(1, 4):
            job = await queue.claim("crash-worker")
            assert job["attempts"] == attempt
            result(storage, queue, job, b"crash sequence")
            assert await queue.attempt_active(job["id"], attempt)
            async with engine.begin() as connection:
                await connection.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == job["id"])
                    .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                )
            assert not await queue.attempt_active(job["id"], attempt)
            # The reaper is global maintenance: other ready rows in this shared
            # schema may also expire, so assert this attempt was reaped, not a total.
            assert await queue.reap_expired() >= 1
            with sync.connect() as connection:
                assert connection.scalar(
                    sa.select(m.jobs.c.state).where(m.jobs.c.id == job["id"])
                ) in ("queued", "failed")
        # The queue is shared across fixtures; inspect this attempt's terminal state
        # instead of assuming no other fixture row is claimable in the same schema.
        with sync.connect() as connection:
            assert (
                connection.scalar(sa.select(m.jobs.c.state).where(m.jobs.c.id == job["id"]))
                == "failed"
            )
        assert not await uploads.cleanup_asset(
            aid
        )  # Reaper already settled and cleaned atomically.
        assert not await uploads.cleanup_asset(aid)
        assert storage.list_objects() == []
        with sync.connect() as connection:
            row = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
            assert row["committed"] == row["reserved"] == 0

    execute(uploads_database, tmp_path, scenario)


def test_retry_staging_removes_crash_output_before_committing_exact_storage(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        aid, _ = await init_uploaded(uploads, owner, project)
        first = await queue.claim("crashed-stage")
        digest = hashlib.sha256(THUMB).hexdigest()
        assert await queue.stage_derivative(
            first["id"],
            first["lease_holder"],
            first["attempts"],
            user,
            chunks=iter([THUMB]),
            size_bytes=len(THUMB),
            sha256=digest,
        )
        async with engine.begin() as connection:
            await connection.execute(
                m.jobs.update()
                .where(m.jobs.c.id == first["id"])
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert await queue.reap_expired() == 1
        second = await queue.claim("retry-stage")
        assert not await queue.stage_derivative(
            first["id"],
            first["lease_holder"],
            first["attempts"],
            user,
            chunks=iter([THUMB]),
            size_bytes=len(THUMB),
            sha256=digest,
        )
        assert await queue.stage_derivative(
            second["id"],
            second["lease_holder"],
            second["attempts"],
            user,
            chunks=iter([THUMB]),
            size_bytes=len(THUMB),
            sha256=digest,
        )
        verified = VerifiedAsset(
            hashlib.sha256(SOURCE).hexdigest(),
            len(SOURCE),
            "image/png",
            100,
            50,
            queue.derivative_key(second),
            len(THUMB),
            digest,
            100,
            50,
        )
        assert (
            await queue.publish_verified(
                second["id"], second["lease_holder"], second["attempts"], user, verified
            )
        ).accepted
        assert (await uploads.get(owner, aid))["state"] == "ready"
        objects = storage.list_objects()
        with sync.connect() as connection:
            balance = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
        assert len(objects) == 2
        assert (
            sum(obj.size_bytes for obj in objects)
            == balance["committed"]
            == len(SOURCE) + len(THUMB)
        )
        assert balance["reserved"] == 0
        assert (await uploads.reconcile())["orphan_keys"] == []

    execute(uploads_database, tmp_path, scenario)


def test_deferred_commit_fence_rejects_lease_expired_after_finalize(
    uploads_database, tmp_path, monkeypatch
):
    from app.modules.uploads.repository import UploadsRepository

    event = UploadsRepository.event

    async def delayed_event(self, *args, **kwargs):
        if kwargs.get("to_state") == "succeeded":
            await asyncio.sleep(1.2)
        return await event(self, *args, **kwargs)

    monkeypatch.setattr(UploadsRepository, "event", delayed_event)

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        aid, _ = await init_uploaded(uploads, owner, project)
        job = await queue.claim("slow-commit")
        verified = result(storage, queue, job)
        assert not (
            await queue.publish_verified(
                job["id"], job["lease_holder"], job["attempts"], user, verified
            )
        ).accepted
        assert (await uploads.get(owner, aid))["state"] == "verifying"
        with sync.connect() as connection:
            row = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
            assert row["committed"] == 0 and row["reserved"] == len(SOURCE) + 1024
        assert await queue.reap_expired() == 1

    execute(uploads_database, tmp_path, scenario, lease=1)


def test_cleanup_uses_persisted_attempt_budget_after_runtime_configuration_reduction(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        aid, _ = await init_uploaded(uploads, owner, project)
        for attempt in range(1, 4):
            job = await queue.claim("crashed-stage")
            assert job["attempts"] == attempt
            result(storage, queue, job)
            async with engine.begin() as connection:
                await connection.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == job["id"])
                    .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                )
            assert await queue.reap_expired() == 1
        uploads.job_max_attempts = 1
        assert not await uploads.cleanup_asset(
            aid
        )  # Reaper already settled and cleaned atomically.
        assert storage.list_objects() == []
        with sync.connect() as connection:
            row = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
            assert row["committed"] == row["reserved"] == 0

    execute(uploads_database, tmp_path, scenario)


def test_staging_lease_expiry_removes_current_bytes_without_releasing_pending_reservation(
    uploads_database, tmp_path
):
    class SlowStaging(LocalObjectStorage):
        def put_immutable_stream(self, *args, **kwargs):
            time.sleep(1.1)
            return super().put_immutable_stream(*args, **kwargs)

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        aid, _ = await init_uploaded(uploads, owner, project)
        job = await queue.claim("slow-stage")
        assert not await queue.stage_derivative(
            job["id"],
            job["lease_holder"],
            job["attempts"],
            user,
            chunks=iter([THUMB]),
            size_bytes=len(THUMB),
            sha256=hashlib.sha256(THUMB).hexdigest(),
        )
        assert len(storage.list_objects()) == 1
        assert (await uploads.get(owner, aid))["state"] == "verifying"
        with sync.connect() as connection:
            row = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.user_id == user,
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
            assert row["committed"] == 0 and row["reserved"] > 0

    execute(uploads_database, tmp_path, scenario, storage_class=SlowStaging, lease=1)


def test_independent_owners_share_atomic_global_storage_ceiling(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owners = [principal(), principal()]
        identities = [await create_project(identity, projects, owner) for owner in owners]
        amount = len(SOURCE) + 1024
        for user, _ in identities:
            await configure(engine, user, amount=amount * 10, global_amount=amount * 3)
        requests = [
            uploads.initialize(
                owners[index % 2],
                identities[index % 2][1],
                idempotency_key=uuid.uuid4().hex,
                body=body(),
            )
            for index in range(12)
        ]
        outcomes = await asyncio.gather(*requests, return_exceptions=True)
        assert sum(not isinstance(result, Exception) for result in outcomes) == 3
        assert all(
            isinstance(result, ApiError) and result.code == "quota_exceeded"
            for result in outcomes
            if isinstance(result, Exception)
        )
        with sync.connect() as connection:
            balance = (
                connection.execute(
                    sa.select(m.quota_balances).where(
                        m.quota_balances.c.scope == "global",
                        m.quota_balances.c.resource == "storage_bytes",
                    )
                )
                .mappings()
                .one()
            )
            assert (
                balance["committed"] == 0
                and balance["reserved"] == balance["limit_amount"] == amount * 3
            )
            assert (
                connection.scalar(
                    sa.select(sa.func.sum(m.quota_reservations.c.amount))
                    .select_from(m.quota_reservations)
                    .where(m.quota_reservations.c.balance_id == balance["id"])
                )
                == amount * 3
            )

    execute(uploads_database, tmp_path, scenario)


def test_publication_fence_upgrade_preserves_prior_release_jobs(uploads_database, tmp_path):
    sync, _ = uploads_database

    def migrate(direction, revision):
        with sync.begin() as connection:
            config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
            config.attributes["connection"] = connection
            getattr(command, direction)(config, revision)

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user)
        ready, _ = await init_uploaded(uploads, owner, project, b"previous release ready")
        first = await queue.claim("old-release-worker")
        assert (await publish(queue, storage, first, b"previous release ready")).accepted
        queued, _ = await init_uploaded(uploads, owner, project, b"previous release queued")
        # Freeze unchanged v1 fixtures, then exercise the historical additive
        # migration. Current repositories must not run against an old schema.
        migrate("downgrade", "0004_capture_uploads")
        with sync.connect() as connection:
            before = [
                dict(row)
                for row in connection.execute(sa.select(m.jobs).order_by(m.jobs.c.id)).mappings()
            ]
        migrate("upgrade", "0005_publication_fence")
        with sync.connect() as connection:
            after = [
                dict(row)
                for row in connection.execute(sa.select(m.jobs).order_by(m.jobs.c.id)).mappings()
            ]
        assert before == after
        migrate("upgrade", "head")
        second = await queue.claim("new-release-worker")
        assert (await publish(queue, storage, second, b"previous release queued")).accepted
        assert (
            (await uploads.get(owner, ready))["state"]
            == (await uploads.get(owner, queued))["state"]
            == "ready"
        )

    execute(uploads_database, tmp_path, scenario)
