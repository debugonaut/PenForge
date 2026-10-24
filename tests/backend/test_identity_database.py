"""Real PostgreSQL identity/claim tests, including concurrent transaction interleavings."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.adapters.auth.base import Principal
from app.core.errors import ApiError
from app.db import models as m
from app.db.session import normalize_database_url
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.service import IdentityService
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
HASH = bytes(range(32))


@pytest.fixture(scope="module")
def identity_database() -> Iterator[tuple[Engine, str]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url is None:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL acceptance")
    normalized = normalize_database_url(url)
    admin = sa.create_engine(normalized)
    schema = "phase01_test_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(normalized, connect_args={"options": f"-csearch_path={schema}"})
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


def principal(*, anonymous: bool = True, uid: str | None = None, **values: Any) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        firebase_uid=uid or uuid.uuid4().hex,
        is_anonymous=anonymous,
        sign_in_provider="anonymous" if anonymous else "password",
        email_verified=not anonymous,
        auth_time=int(NOW.timestamp()),
        token_issued_at=int(NOW.timestamp()),
        **values,
    )


def run(
    database: tuple[Engine, str],
    scenario: Callable[[IdentityService, AsyncEngine], Awaitable[None]],
) -> None:
    async def execute() -> None:
        sync_engine, schema = database
        engine = create_async_engine(
            sync_engine.url, connect_args={"options": f"-csearch_path={schema}"}, pool_size=20
        )
        try:
            await scenario(IdentityService(engine, clock=lambda: NOW), engine)
        finally:
            await engine.dispose()

    asyncio.run(execute())


def put(connection: sa.Connection, table: sa.Table, **values: Any) -> uuid.UUID:
    key = values.setdefault("id", uuid.uuid4())
    connection.execute(table.insert().values(**values))
    return key


def seed_project(connection: sa.Connection, owner: uuid.UUID, **values: Any) -> uuid.UUID:
    return put(
        connection, m.projects, owner_user_id=owner, name="Preserved", script_code="Latn", **values
    )


def seed_asset(
    connection: sa.Connection, owner: uuid.UUID, project: uuid.UUID, **values: Any
) -> uuid.UUID:
    return put(
        connection,
        m.assets,
        owner_user_id=owner,
        project_id=project,
        kind="original_upload",
        storage_backend="local_fs",
        storage_key=f"u/{owner}/p/{project}/{uuid.uuid4()}",
        mime_type="image/png",
        size_bytes=10,
        sha256=HASH,
        state="ready",
        verified_at=NOW,
        retention_class="original",
        **values,
    )


def seed_job(
    connection: sa.Connection, owner: uuid.UUID, project: uuid.UUID, **values: Any
) -> uuid.UUID:
    return put(
        connection,
        m.jobs,
        owner_user_id=owner,
        project_id=project,
        job_type="asset_verify",
        payload={},
        payload_schema_version=1,
        **values,
    )


def seed_balance(connection: sa.Connection, owner: uuid.UUID | None, **values: Any) -> uuid.UUID:
    return put(
        connection,
        m.quota_balances,
        user_id=owner,
        scope="user" if owner else "global",
        resource="storage_bytes",
        window_kind="lifetime",
        window_start=NOW.date(),
        limit_amount=20,
        **values,
    )


def test_concurrent_first_requests_create_one_authoritative_user(
    identity_database: tuple[Engine, str],
) -> None:
    guest = principal()

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        assert not await service.identity_exists(guest.firebase_uid)
        results = await asyncio.gather(*(service.me(guest, b"h" * 32) for _ in range(16)))
        assert len({item["user_id"] for item in results}) == 1
        assert results[0]["user_id"] != guest.user_id
        assert results[0]["guest_creations"] == {
            "limit": 3,
            "used": 0,
            "remaining": 3,
            "applicable": True,
        }
        assert results[0]["quotas"] == {"configured": False, "over_limit": False, "balances": []}
        assert await service.identity_exists(guest.firebase_uid)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.users)
                    .where(m.users.c.firebase_uid == guest.firebase_uid)
                )
                == 1
            )
            row = (
                (
                    await connection.execute(
                        sa.select(m.guest_usage).where(
                            m.guest_usage.c.user_id == results[0]["user_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert row["first_seen_ip_hash"] == b"h" * 32

    run(identity_database, scenario)


def test_in_place_link_preserves_history_and_rejects_old_anonymous_token(
    identity_database: tuple[Engine, str],
) -> None:
    guest = principal()
    registered = replace(
        guest, is_anonymous=False, sign_in_provider="google.com", email_verified=True
    )

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        before = await service.me(guest)
        with identity_database[0].begin() as connection:
            project = seed_project(connection, before["user_id"])
            connection.execute(
                m.guest_usage.update()
                .where(m.guest_usage.c.user_id == before["user_id"])
                .values(creations_used=3)
            )
        after = await service.me(registered)
        assert after["user_id"] == before["user_id"] and after["created_at"] == before["created_at"]
        assert after["tier"] == "free_user" and after["guest_creations"]["used"] == 3
        assert after["guest_creations"]["applicable"] is False
        linked = await service.claim(registered, guest)
        assert linked["mode"] == "linked" and linked["transferred"]["projects"] == 0
        assert await service.claim(registered, guest) == linked
        with pytest.raises(ApiError) as failure:
            await service.me(guest)
        assert failure.value.code == "invalid_token"
        async with engine.connect() as connection:
            assert (
                await IdentityRepository(connection).project(before["user_id"], project)
            ) is not None

    run(identity_database, scenario)


def test_claim_preserves_colliding_assets_jobs_lineage_quota_and_exact_replay(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source, target = await service.me(guest), await service.me(registered)
        sid, tid = source["user_id"], target["user_id"]
        with identity_database[0].begin() as connection:
            project, other = seed_project(connection, sid), seed_project(connection, tid)
            original, canonical = (
                seed_asset(connection, sid, project),
                seed_asset(connection, tid, other),
            )
            key = connection.scalar(
                sa.select(m.assets.c.storage_key).where(m.assets.c.id == original)
            )
            job, target_job = (
                seed_job(connection, sid, project, idempotency_key="same"),
                seed_job(connection, tid, other, idempotency_key="same"),
            )
            connection.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job)
                .values(
                    state="running",
                    attempts=1,
                    lease_holder="worker",
                    lease_expires_at=NOW + timedelta(minutes=1),
                )
            )
            glyph = put(connection, m.glyphs, project_id=project, codepoints=[97])
            revision = put(
                connection,
                m.glyph_revisions,
                glyph_id=glyph,
                seq=1,
                origin="user_capture",
                approval="approved",
                outline_inline={"contours": []},
                content_hash=HASH,
                created_by_user_id=sid,
                approved_by_user_id=sid,
                approved_at=NOW,
            )
            connection.execute(
                m.glyphs.update().where(m.glyphs.c.id == glyph).values(active_revision_id=revision)
            )
            snapshot = put(
                connection,
                m.project_snapshots,
                project_id=project,
                kind="milestone",
                manifest={"glyphs": {str(glyph): str(revision)}},
                manifest_sha256=HASH,
                created_by_user_id=sid,
            )
            balance, destination_balance = (
                seed_balance(connection, sid, committed=10, reserved=8),
                seed_balance(connection, tid, committed=15, reserved=2),
            )
            global_balance = seed_balance(connection, None, committed=25, reserved=10)
            reservation = put(
                connection,
                m.quota_reservations,
                balance_id=balance,
                user_id=sid,
                job_id=job,
                resource="storage_bytes",
                amount=8,
                expires_at=NOW + timedelta(hours=1),
            )
            global_reservation = put(
                connection,
                m.quota_reservations,
                balance_id=global_balance,
                user_id=sid,
                job_id=job,
                resource="storage_bytes",
                amount=8,
                expires_at=NOW + timedelta(hours=1),
            )
            connection.execute(
                m.guest_usage.update()
                .where(m.guest_usage.c.user_id == sid)
                .values(creations_used=3)
            )
        response = await service.claim(registered, guest, request_id=str(uuid.uuid4()))
        assert response["transferred"] == {"projects": 1, "assets": 1, "jobs": 1, "reservations": 2}
        assert response["over_limit"] is True and response["guest_creations_preserved"] == 3
        assert await service.claim(registered, guest) == response
        after = await service.me(registered)
        assert after["guest_creations"]["used"] == 3
        assert after["quotas"]["balances"][0] | {} == {
            "resource": "storage_bytes",
            "window_kind": "lifetime",
            "window_start": NOW.date(),
            "limit_amount": 20,
            "committed": 25,
            "reserved": 10,
            "available": 0,
            "over_limit": True,
        }
        with pytest.raises(ApiError) as failure:
            await service.me(guest)
        assert failure.value.code == "account_merged"
        async with engine.connect() as connection:
            repo = IdentityRepository(connection)
            assert (
                await repo.project(sid, project) is None and await repo.asset(sid, original) is None
            )
            assert await repo.project(tid, project) is not None
            inventory = await repo.purge_inventory(tid, project)
            assert [r["storage_key"] for r in inventory] == [key] and str(sid) in key
            asset = (
                (await connection.execute(sa.select(m.assets).where(m.assets.c.id == original)))
                .mappings()
                .one()
            )
            assert (
                asset["state"] == "ready"
                and asset["duplicate_of"] == canonical
                and asset["sha256"] == HASH
            )
            jobs = (
                (
                    await connection.execute(
                        sa.select(m.jobs).where(m.jobs.c.id.in_([job, target_job]))
                    )
                )
                .mappings()
                .all()
            )
            assert len(jobs) == 2 and {r["idempotency_key"] for r in jobs} == {"same"}
            running = next(r for r in jobs if r["id"] == job)
            assert (
                running["state"] == "running"
                and running["lease_holder"] == "worker"
                and running["idempotency_scope_user_id"] == sid
            )
            assert (
                await connection.scalar(
                    sa.select(m.quota_reservations.c.balance_id).where(
                        m.quota_reservations.c.id == reservation
                    )
                )
                == destination_balance
            )
            assert (
                await connection.scalar(
                    sa.select(m.quota_reservations.c.balance_id).where(
                        m.quota_reservations.c.id == global_reservation
                    )
                )
                == global_balance
            )
            assert (
                await connection.scalar(
                    sa.select(m.quota_balances.c.reserved).where(
                        m.quota_balances.c.id == global_balance
                    )
                )
                == 10
            )
            revision_row = (
                (
                    await connection.execute(
                        sa.select(m.glyph_revisions).where(m.glyph_revisions.c.id == revision)
                    )
                )
                .mappings()
                .one()
            )
            assert (
                revision_row["created_by_user_id"] == sid
                and revision_row["approved_by_user_id"] == sid
                and revision_row["content_hash"] == HASH
            )
            assert (
                await connection.scalar(
                    sa.select(m.project_snapshots.c.created_by_user_id).where(
                        m.project_snapshots.c.id == snapshot
                    )
                )
                == sid
            )
            assert (
                await connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.audit_events)
                    .where(m.audit_events.c.resource_id == sid)
                )
                == 1
            )
        with identity_database[0].begin() as connection:
            connection.execute(
                m.quota_balances.update()
                .where(m.quota_balances.c.id == destination_balance)
                .values(committed=0, reserved=0)
            )
        assert await service.claim(registered, guest) == response

    run(identity_database, scenario)


def test_competing_claims_have_one_winner_and_replay_does_not_duplicate_history(
    identity_database: tuple[Engine, str],
) -> None:
    guest, first, second = principal(), principal(anonymous=False), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source = await service.me(guest)
        await service.me(first)
        await service.me(second)
        with identity_database[0].begin() as connection:
            seed_project(connection, source["user_id"])
        results = await asyncio.gather(
            service.claim(first, guest), service.claim(second, guest), return_exceptions=True
        )
        winners = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, ApiError)]
        assert len(winners) == len(failures) == 1 and failures[0].code == "already_claimed"
        winner = first if isinstance(results[0], dict) else second
        assert await service.claim(winner, guest) == winners[0]

    run(identity_database, scenario)


@pytest.mark.parametrize("state", ["suspended", "deleting"])
@pytest.mark.parametrize("subject", ["source", "destination"])
def test_claim_rejects_inactive_accounts_atomically(
    identity_database: tuple[Engine, str], state: str, subject: str
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source, target = await service.me(guest), await service.me(registered)
        user = source if subject == "source" else target
        with identity_database[0].begin() as connection:
            project = seed_project(connection, source["user_id"])
            connection.execute(
                m.users.update().where(m.users.c.id == user["user_id"]).values(account_state=state)
            )
        with pytest.raises(ApiError) as failure:
            await service.claim(registered, guest)
        assert failure.value.code == "account_" + state
        async with engine.connect() as connection:
            assert (
                await IdentityRepository(connection).project(source["user_id"], project) is not None
            )

    run(identity_database, scenario)


@pytest.mark.parametrize("age", [601, 3600, -1])
def test_stale_or_future_guest_auth_time_rejected(
    identity_database: tuple[Engine, str], age: int
) -> None:
    guest = replace(principal(), auth_time=int(NOW.timestamp()) - age)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        with pytest.raises(ApiError) as failure:
            await service.claim(principal(anonymous=False), guest)
        assert failure.value.code == "stale_guest_token"

    run(identity_database, scenario)


def test_anonymous_destination_and_registered_source_rejected(
    identity_database: tuple[Engine, str],
) -> None:
    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        for destination, guest in [
            (principal(), principal()),
            (principal(anonymous=False), principal(anonymous=False)),
        ]:
            with pytest.raises(ApiError) as failure:
                await service.claim(destination, guest)
            assert failure.value.code == "invalid_claim"

    run(identity_database, scenario)


def test_new_job_before_claim_commits_is_included_in_atomic_transfer(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source, target = await service.me(guest), await service.me(registered)
        with identity_database[0].begin() as connection:
            project = seed_project(connection, source["user_id"])
        async with engine.connect() as connection:
            transaction = await connection.begin()
            job = uuid.uuid4()
            await connection.execute(
                m.jobs.insert().values(
                    id=job,
                    owner_user_id=source["user_id"],
                    project_id=project,
                    job_type="asset_verify",
                    payload={},
                    payload_schema_version=1,
                )
            )
            task = asyncio.create_task(service.claim(registered, guest))
            await asyncio.sleep(0.03)
            assert not task.done()
            await transaction.commit()
        result = await task
        assert result["transferred"]["jobs"] == 1
        async with engine.connect() as connection:
            assert (
                await connection.scalar(sa.select(m.jobs.c.owner_user_id).where(m.jobs.c.id == job))
                == target["user_id"]
            )

    run(identity_database, scenario)


def test_stale_guest_cannot_insert_after_claim_user_lock(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source = await service.me(guest)
        await service.me(registered)
        with identity_database[0].begin() as connection:
            project = seed_project(connection, source["user_id"])
        # Gate source lock acquisition so the racing insert is waiting when merge commits.
        async with engine.connect() as gate:
            tx = await gate.begin()
            await gate.execute(
                sa.select(m.users.c.id).where(m.users.c.id == source["user_id"]).with_for_update()
            )
            claim_task = asyncio.create_task(service.claim(registered, guest))
            await asyncio.sleep(0.03)

            async def stale_insert() -> None:
                async with engine.begin() as connection:
                    await connection.execute(
                        m.jobs.insert().values(
                            id=uuid.uuid4(),
                            owner_user_id=source["user_id"],
                            project_id=project,
                            job_type="asset_verify",
                            payload={},
                            payload_schema_version=1,
                        )
                    )

            insert_task = asyncio.create_task(stale_insert())
            await asyncio.sleep(0.03)
            await tx.commit()
        await claim_task
        with pytest.raises(DBAPIError):
            await insert_task

    run(identity_database, scenario)


def test_claim_withdraws_contributions_without_granting_destination_consent(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source, target = await service.me(guest), await service.me(registered)
        with identity_database[0].begin() as connection:
            project = seed_project(connection, source["user_id"])
            asset = seed_asset(connection, source["user_id"], project)
            consent = put(
                connection,
                m.training_consents,
                user_id=source["user_id"],
                purpose="model_training",
                policy_version="historic",
            )
            sample = put(
                connection,
                m.samples,
                project_id=project,
                asset_id=asset,
                source_type="canvas_drawing",
                consent_id_at_upload=consent,
            )
            contribution = put(
                connection,
                m.dataset_contributions,
                sample_id=sample,
                contributor_ref=HASH,
                consent_id=consent,
            )
        await service.claim(registered, guest)
        async with engine.begin() as connection:
            assert (
                await connection.scalar(
                    sa.select(m.dataset_contributions.c.status).where(
                        m.dataset_contributions.c.id == contribution
                    )
                )
                == "withdrawn"
            )
            assert (
                await connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.training_consents)
                    .where(m.training_consents.c.user_id == target["user_id"])
                )
                == 0
            )
            # Historical consent can still be read/retained when sample assessment changes.
            await connection.execute(
                m.samples.update().where(m.samples.c.id == sample).values(state="analyzed")
            )

    run(identity_database, scenario)


def test_migration_upgrades_seeded_phase00_and_downgrades_without_claims(
    identity_database: tuple[Engine, str],
) -> None:
    engine = identity_database[0]
    schema = "phase01_upgrade_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.exec_driver_sql(f'SET LOCAL search_path="{schema}"')
        config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "0001_initial")
        owner = put(connection, m.users, firebase_uid=uuid.uuid4().hex)
        job = uuid.uuid4()
        connection.exec_driver_sql(
            "INSERT INTO jobs(id,owner_user_id,job_type,payload,payload_schema_version,idempotency_key) VALUES (%s,%s,'asset_verify','{}',1,'preserved')",
            (job, owner),
        )
        command.upgrade(config, "head")
        assert (
            connection.scalar(
                sa.select(m.jobs.c.idempotency_scope_user_id).where(m.jobs.c.id == job)
            )
            == owner
        )
        command.downgrade(config, "0001_initial")
        command.upgrade(config, "head")
        connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


def test_expired_quota_windows_are_history_and_not_current_allowance(
    identity_database: tuple[Engine, str],
) -> None:
    account = principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        identity = await service.me(account)
        with identity_database[0].begin() as connection:
            for kind, start in [
                ("day", NOW.date() - timedelta(days=1)),
                ("month", NOW.date().replace(day=1) - timedelta(days=1)),
            ]:
                put(
                    connection,
                    m.quota_balances,
                    user_id=identity["user_id"],
                    scope="user",
                    resource="exports",
                    window_kind=kind,
                    window_start=start,
                    limit_amount=0,
                    committed=9,
                )
            put(
                connection,
                m.quota_balances,
                user_id=identity["user_id"],
                scope="user",
                resource="exports",
                window_kind="day",
                window_start=NOW.date(),
                limit_amount=10,
                committed=2,
            )
        result = await service.me(account)
        assert result["quotas"]["over_limit"] is False
        assert len(result["quotas"]["balances"]) == 1
        assert result["quotas"]["balances"][0]["available"] == 8

    run(identity_database, scenario)


def test_account_deletion_lock_wins_before_claim_without_mixed_ownership(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source, target = await service.me(guest), await service.me(registered)
        with identity_database[0].begin() as connection:
            project = seed_project(connection, source["user_id"])
            asset = seed_asset(connection, source["user_id"], project)
            job = seed_job(connection, source["user_id"], project)
        async with engine.connect() as connection:
            transaction = await connection.begin()
            await connection.execute(
                m.users.update()
                .where(m.users.c.id == target["user_id"])
                .values(account_state="deleting")
            )
            task = asyncio.create_task(service.claim(registered, guest))
            await asyncio.sleep(0.03)
            assert not task.done()
            await transaction.commit()
        with pytest.raises(ApiError) as failure:
            await task
        assert failure.value.code == "account_deleting"
        async with engine.connect() as connection:
            for table, key in [(m.projects, project), (m.assets, asset), (m.jobs, job)]:
                assert (
                    await connection.scalar(
                        sa.select(table.c.owner_user_id).where(table.c.id == key)
                    )
                    == source["user_id"]
                )

    run(identity_database, scenario)


def test_job_execution_serializes_with_claim_and_preserves_lease(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source, target = await service.me(guest), await service.me(registered)
        with identity_database[0].begin() as connection:
            project = seed_project(connection, source["user_id"])
            job = seed_job(connection, source["user_id"], project)
        async with engine.connect() as connection:
            transaction = await connection.begin()
            # Required lock order for future worker services: user before child.
            await connection.execute(
                sa.select(m.users.c.id)
                .where(m.users.c.id == source["user_id"])
                .with_for_update(read=True)
            )
            await connection.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job)
                .values(
                    state="running",
                    attempts=1,
                    lease_holder="active-worker",
                    lease_expires_at=NOW + timedelta(seconds=60),
                )
            )
            task = asyncio.create_task(service.claim(registered, guest))
            await asyncio.sleep(0.03)
            assert not task.done()
            await transaction.commit()
        await task
        async with engine.connect() as connection:
            row = (
                (await connection.execute(sa.select(m.jobs).where(m.jobs.c.id == job)))
                .mappings()
                .one()
            )
            assert row["owner_user_id"] == target["user_id"] and row["state"] == "running"
            assert row["lease_holder"] == "active-worker" and row["attempts"] == 1

    run(identity_database, scenario)


def test_direct_owner_write_to_merged_identity_and_receipt_mutation_rejected(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source = await service.me(guest)
        await service.me(registered)
        await service.claim(registered, guest)
        async with engine.connect() as connection:
            for statement in [
                m.projects.insert().values(
                    id=uuid.uuid4(),
                    owner_user_id=source["user_id"],
                    name="Stale",
                    script_code="Latn",
                ),
                m.identity_claims.update()
                .where(m.identity_claims.c.guest_user_id == source["user_id"])
                .values(response_body={}),
            ]:
                with pytest.raises(DBAPIError):
                    async with connection.begin():
                        await connection.execute(statement)

    run(identity_database, scenario)


def test_identity_upgrade_is_monotonic_and_retains_nondefault_tier(
    identity_database: tuple[Engine, str],
) -> None:
    guest = principal()
    account = replace(guest, is_anonymous=False, sign_in_provider="password")

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        before = await service.me(guest)
        with identity_database[0].begin() as connection:
            connection.execute(
                m.users.update()
                .where(m.users.c.id == before["user_id"])
                .values(tier="operator-configured")
            )
        assert (await service.me(account))["tier"] == "operator-configured"
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError):
                async with connection.begin():
                    await connection.execute(
                        m.users.update()
                        .where(m.users.c.id == before["user_id"])
                        .values(is_anonymous=True)
                    )

    run(identity_database, scenario)


def test_owner_inventory_and_claim_history_indexes_are_usable(
    identity_database: tuple[Engine, str],
) -> None:
    with identity_database[0].begin() as connection:
        connection.exec_driver_sql("SET LOCAL enable_seqscan=off")
        queries = [
            (
                "SELECT id FROM assets WHERE owner_user_id='00000000-0000-0000-0000-000000000001' AND state<>'deleted'",
                "assets_owner_inventory_idx",
            ),
            (
                "SELECT guest_user_id FROM identity_claims WHERE destination_user_id='00000000-0000-0000-0000-000000000001'",
                "identity_claims_destination_idx",
            ),
        ]
        for query, index in queries:
            assert index in "\n".join(connection.exec_driver_sql("EXPLAIN " + query).scalars())


def test_parallel_identical_claims_replay_exactly_once(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source = await service.me(guest)
        await service.me(registered)
        with identity_database[0].begin() as connection:
            seed_project(connection, source["user_id"])
            connection.execute(
                m.guest_usage.update()
                .where(m.guest_usage.c.user_id == source["user_id"])
                .values(creations_used=2)
            )
        responses = await asyncio.gather(*(service.claim(registered, guest) for _ in range(8)))
        assert all(response == responses[0] for response in responses)
        assert responses[0]["transferred"]["projects"] == 1
        assert (await service.me(registered))["guest_creations"]["used"] == 2
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(m.identity_claims)
                    .where(m.identity_claims.c.guest_user_id == source["user_id"])
                )
                == 1
            )

    run(identity_database, scenario)


def test_claim_moves_standalone_assets_jobs_and_noncolliding_balance(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered = principal(), principal(anonymous=False)

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source, target = await service.me(guest), await service.me(registered)
        with identity_database[0].begin() as connection:
            asset = put(
                connection,
                m.assets,
                owner_user_id=source["user_id"],
                kind="checkpoint",
                storage_backend="local_fs",
                storage_key=f"u/{source['user_id']}/historical/checkpoint",
                mime_type="application/json",
                size_bytes=10,
                retention_class="checkpoint",
            )
            job = put(
                connection,
                m.jobs,
                owner_user_id=source["user_id"],
                job_type="maintenance_reconcile",
                payload={},
                payload_schema_version=1,
            )
            balance = seed_balance(connection, source["user_id"], committed=4, reserved=3)
            reservation = put(
                connection,
                m.quota_reservations,
                balance_id=balance,
                user_id=source["user_id"],
                job_id=job,
                resource="storage_bytes",
                amount=3,
                expires_at=NOW + timedelta(hours=1),
            )
        result = await service.claim(registered, guest)
        assert result["transferred"] == {"projects": 0, "assets": 1, "jobs": 1, "reservations": 1}
        async with engine.connect() as connection:
            for table, key, column in [
                (m.assets, asset, "owner_user_id"),
                (m.jobs, job, "owner_user_id"),
                (m.quota_balances, balance, "user_id"),
                (m.quota_reservations, reservation, "user_id"),
            ]:
                assert (
                    await connection.scalar(sa.select(table.c[column]).where(table.c.id == key))
                    == target["user_id"]
                )
            row = (
                (
                    await connection.execute(
                        sa.select(m.quota_balances).where(m.quota_balances.c.id == balance)
                    )
                )
                .mappings()
                .one()
            )
            assert row["committed"] == 4 and row["reserved"] == 3 and row["limit_amount"] == 20
            assert any(
                item["id"] == asset
                for item in await IdentityRepository(connection).purge_inventory(target["user_id"])
            )

    run(identity_database, scenario)


def test_owner_service_returns_404_to_third_user_and_blocks_stale_guest(
    identity_database: tuple[Engine, str],
) -> None:
    guest, registered, stranger = (
        principal(),
        principal(anonymous=False),
        principal(anonymous=False),
    )

    async def scenario(service: IdentityService, engine: AsyncEngine) -> None:
        source, target = await service.me(guest), await service.me(registered)
        await service.me(stranger)
        with identity_database[0].begin() as connection:
            project = seed_project(connection, source["user_id"])
            asset = seed_asset(connection, source["user_id"], project)
        assert (await service.project_for_owner(guest, project))["owner_user_id"] == source[
            "user_id"
        ]
        assert (await service.asset_for_owner(guest, asset))["owner_user_id"] == source["user_id"]
        await service.claim(registered, guest)
        for lookup, resource in [
            (service.project_for_owner, project),
            (service.asset_for_owner, asset),
        ]:
            assert (await lookup(registered, resource))["owner_user_id"] == target["user_id"]
            with pytest.raises(ApiError) as failure:
                await lookup(stranger, resource)
            assert failure.value.code == "not_found" and failure.value.status == 404
            with pytest.raises(ApiError) as failure:
                await lookup(guest, resource)
            assert failure.value.code == "account_merged"

    run(identity_database, scenario)
