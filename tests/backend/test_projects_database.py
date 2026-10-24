"""Phase 02 real-PostgreSQL project races and recovery invariants."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable, Iterator
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
from app.modules.identity.service import IdentityService
from app.modules.projects.service import ProjectsService
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def project_database() -> Iterator[tuple[Engine, str]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url is None:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL project acceptance")
    normalized = normalize_database_url(url)
    admin = sa.create_engine(normalized)
    schema = "phase02_test_" + uuid.uuid4().hex
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


def principal(*, anonymous: bool = True, uid: str | None = None) -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        firebase_uid=uid or uuid.uuid4().hex,
        is_anonymous=anonymous,
        sign_in_provider="anonymous" if anonymous else "password",
        email_verified=not anonymous,
        auth_time=int(NOW.timestamp()),
        token_issued_at=int(NOW.timestamp()),
    )


def run(
    database: tuple[Engine, str],
    scenario: Callable[[Engine, AsyncEngine, IdentityService, ProjectsService], Awaitable[None]],
    *,
    clock: Callable[[], datetime] = lambda: NOW,
) -> None:
    def execute() -> None:
        sync_engine, schema = database
        engine = create_async_engine(
            sync_engine.url,
            connect_args={"options": f"-csearch_path={schema}"},
            pool_size=30,
        )
        identity = IdentityService(engine, clock=clock)
        projects = ProjectsService(
            engine,
            identity,
            cursor_secret=b"p" * 32,
            clock=clock,
        )

        async def work() -> None:
            try:
                await scenario(sync_engine, engine, identity, projects)
            finally:
                await engine.dispose()

        asyncio.run(work())

    execute()


async def create(
    service: ProjectsService,
    owner: Principal,
    key: str,
    name: str = "Practice hand",
    *,
    style_controls: dict[str, float] | None = None,
) -> tuple[bool, int, dict[str, Any]]:
    return await service.create(
        owner,
        idempotency_key=key,
        name=name,
        script_code="Latn",
        style_controls=style_controls or {},
    )


def test_guest_creation_boundary_and_parallel_identical_key_are_atomic(
    project_database: tuple[Engine, str],
) -> None:
    guest = principal()

    async def scenario(
        sync: Engine, _async: AsyncEngine, _identity: IdentityService, service: ProjectsService
    ) -> None:
        results = await asyncio.gather(
            *(create(service, guest, f"guest-create-{i}", f"Draft {i}") for i in range(12)),
            return_exceptions=True,
        )
        successes = [result for result in results if not isinstance(result, Exception)]
        rejected = [result for result in results if isinstance(result, ApiError)]
        assert len(successes) == 3
        assert all(result[2]["updated_at"].endswith("+00:00") for result in successes)
        assert len(rejected) == 9
        assert all(item.code == "guest_limit_reached" and item.status == 403 for item in rejected)
        with sync.connect() as connection:
            usage = connection.execute(
                sa.select(m.guest_usage.c.creations_used)
                .join(m.users, m.users.c.id == m.guest_usage.c.user_id)
                .where(m.users.c.firebase_uid == guest.firebase_uid)
            ).scalar_one()
            rows = connection.execute(
                sa.select(sa.func.count())
                .select_from(m.projects)
                .join(m.users, m.users.c.id == m.projects.c.owner_user_id)
                .where(m.users.c.firebase_uid == guest.firebase_uid)
            ).scalar_one()
        assert usage == rows == 3
        completed = successes[0]
        completed_name = completed[2]["name"]
        completed_key = "guest-create-" + completed_name.removeprefix("Draft ")
        replay_after_limit = await create(service, guest, completed_key, completed_name)
        assert replay_after_limit[0] is True
        assert replay_after_limit[2]["id"] == completed[2]["id"]
        with sync.connect() as connection:
            used_after_replay = connection.execute(
                sa.select(m.guest_usage.c.creations_used)
                .join(m.users, m.users.c.id == m.guest_usage.c.user_id)
                .where(m.users.c.firebase_uid == guest.firebase_uid)
            ).scalar_one()
        assert used_after_replay == 3

        parallel_owner = principal()
        parallel = await asyncio.gather(
            *(create(service, parallel_owner, "same-request-key", "One project") for _ in range(10))
        )
        assert len({item[2]["id"] for item in parallel}) == 1
        assert sum(not item[0] for item in parallel) == 1
        assert sum(item[0] for item in parallel) == 9
        assert all(item[1] == 201 and item[2] == parallel[0][2] for item in parallel)
        changed = await asyncio.gather(
            create(service, parallel_owner, "same-request-key", "Changed body"),
            return_exceptions=True,
        )
        assert isinstance(changed[0], ApiError)
        assert (changed[0].code, changed[0].status) == ("idempotency_key_reuse", 422)
        with sync.connect() as connection:
            count = connection.execute(
                sa.select(sa.func.count())
                .select_from(m.projects)
                .join(m.users, m.users.c.id == m.projects.c.owner_user_id)
                .where(m.users.c.firebase_uid == parallel_owner.firebase_uid)
            ).scalar_one()
            used = connection.execute(
                sa.select(m.guest_usage.c.creations_used)
                .join(m.users, m.users.c.id == m.guest_usage.c.user_id)
                .where(m.users.c.firebase_uid == parallel_owner.firebase_uid)
            ).scalar_one()
        assert count == used == 1

        other_owner = principal()
        same_key_other_identity = await create(
            service, other_owner, "same-request-key", "One project"
        )
        assert same_key_other_identity[0] is False
        assert same_key_other_identity[2]["id"] != parallel[0][2]["id"]

    run(project_database, scenario)


def test_idempotency_expiry_replays_until_ttl_then_allows_new_operation(
    project_database: tuple[Engine, str],
) -> None:
    guest = principal()
    clock_value = [NOW]

    async def scenario(
        _sync: Engine, _async: AsyncEngine, _identity: IdentityService, service: ProjectsService
    ) -> None:
        first = await create(service, guest, "expiry-key", "First")
        replay = await create(service, guest, "expiry-key", "First")
        assert replay[0] is True and replay[2] == first[2]
        clock_value[0] += timedelta(days=1)
        after_expiration = await create(service, guest, "expiry-key", "First")
        assert after_expiration[0] is False
        assert after_expiration[2]["id"] != first[2]["id"]

    run(project_database, scenario, clock=lambda: clock_value[0])


def test_keyset_pages_are_deterministic_owner_bound_and_hide_foreign_resources(
    project_database: tuple[Engine, str],
) -> None:
    owner, other = principal(anonymous=False), principal(anonymous=False)

    async def scenario(
        _sync: Engine, _async: AsyncEngine, _identity: IdentityService, service: ProjectsService
    ) -> None:
        for index in range(8):
            await create(service, owner, f"list-{index}", f"Project {index}")
        first = await service.list(owner, limit=3, cursor=None)
        second = await service.list(owner, limit=3, cursor=first["next_cursor"])
        third = await service.list(owner, limit=3, cursor=second["next_cursor"])
        all_ids = [row["id"] for page in (first, second, third) for row in page["items"]]
        assert len(all_ids) == 8 and len(set(all_ids)) == 8
        assert third["next_cursor"] is None
        with pytest.raises(ApiError) as foreign_cursor:
            await service.list(other, limit=3, cursor=first["next_cursor"])
        assert (foreign_cursor.value.code, foreign_cursor.value.status) == ("validation_error", 400)
        with pytest.raises(ApiError) as tampered_cursor:
            cursor = first["next_cursor"]
            replacement = "A" if cursor[0] != "A" else "B"
            await service.list(owner, limit=3, cursor=replacement + cursor[1:])
        assert tampered_cursor.value.code == "validation_error"
        foreign_id = await service.get(owner, uuid.UUID(first["items"][0]["id"]))
        assert foreign_id["name"].startswith("Project ")
        with pytest.raises(ApiError) as hidden:
            await service.get(other, uuid.UUID(foreign_id["id"]))
        assert (hidden.value.code, hidden.value.status) == ("not_found", 404)
        with pytest.raises(ApiError) as hidden_conflict:
            await service.update(
                other,
                uuid.UUID(foreign_id["id"]),
                if_match='"v1"',
                changes={"name": "attacker detail"},
            )
        assert hidden_conflict.value.code == "not_found"
        assert hidden_conflict.value.context is None

    run(project_database, scenario)


def test_concurrent_edits_return_412_and_owner_recovers_without_losing_local_text(
    project_database: tuple[Engine, str],
) -> None:
    owner = principal(anonymous=False)

    async def scenario(
        _sync: Engine, _async: AsyncEngine, _identity: IdentityService, service: ProjectsService
    ) -> None:
        _, _, project = await create(service, owner, "edit-create", "Original")
        project_id = uuid.UUID(project["id"])
        outcomes = await asyncio.gather(
            service.update(owner, project_id, if_match='"v1"', changes={"name": "tab A"}),
            service.update(owner, project_id, if_match='"v1"', changes={"name": "tab B"}),
            return_exceptions=True,
        )
        successes = [item for item in outcomes if not isinstance(item, Exception)]
        conflicts = [item for item in outcomes if isinstance(item, ApiError)]
        assert len(successes) == len(conflicts) == 1
        conflict = conflicts[0]
        assert (conflict.code, conflict.status) == ("revision_conflict", 412)
        assert conflict.context["current_row_version"] == 2
        assert conflict.context["current_project"]["name"] == successes[0]["name"]
        assert "tab A" in str(conflict.context) or "tab B" in str(conflict.context)
        with pytest.raises(ApiError) as required:
            await service.update(owner, project_id, if_match=None, changes={"name": "kept locally"})
        assert required.value.status == 428
        recovered = await service.update(
            owner, project_id, if_match='"v2"', changes={"name": "kept locally"}
        )
        assert recovered["name"] == "kept locally" and recovered["row_version"] == 3

    run(project_database, scenario)


def test_claimed_project_rejects_stale_guest_save_and_preserves_usage_and_work(
    project_database: tuple[Engine, str],
) -> None:
    guest = principal()
    destination = principal(anonymous=False)

    async def scenario(
        sync: Engine,
        _async: AsyncEngine,
        identity: IdentityService,
        service: ProjectsService,
    ) -> None:
        _, _, original = await create(service, guest, "claim-project", "Guest work")
        project_id = uuid.UUID(original["id"])
        claim = await identity.claim(destination, guest)
        assert claim["transferred"]["projects"] == 1
        with pytest.raises(ApiError) as stale_guest:
            await service.update(
                guest, project_id, if_match='"v1"', changes={"name": "stale local overwrite"}
            )
        assert (stale_guest.value.code, stale_guest.value.status) == ("account_merged", 403)
        current = await service.get(destination, project_id)
        assert current["name"] == "Guest work"
        recovered = await service.update(
            destination, project_id, if_match='"v1"', changes={"name": "recovered draft"}
        )
        assert recovered["name"] == "recovered draft"
        with sync.connect() as connection:
            guest_usage = connection.execute(
                sa.select(m.guest_usage.c.creations_used)
                .join(m.users, m.users.c.id == m.guest_usage.c.user_id)
                .where(m.users.c.firebase_uid == guest.firebase_uid)
            ).scalar_one()
        assert guest_usage == 1
        replay = await identity.claim(destination, guest)
        assert replay == claim

    run(project_database, scenario)


def test_delete_checks_owner_and_precondition_then_reports_unavailable_without_hard_delete(
    project_database: tuple[Engine, str],
) -> None:
    owner, other = principal(anonymous=False), principal(anonymous=False)

    async def scenario(
        sync: Engine, _async: AsyncEngine, _identity: IdentityService, service: ProjectsService
    ) -> None:
        _, _, project = await create(service, owner, "delete-check", "Still here")
        project_id = uuid.UUID(project["id"])
        with pytest.raises(ApiError) as foreign:
            await service.request_delete(other, project_id, if_match='"v1"')
        assert (foreign.value.code, foreign.value.status) == ("not_found", 404)
        with pytest.raises(ApiError) as missing:
            await service.request_delete(owner, project_id, if_match=None)
        assert missing.value.status == 428
        with pytest.raises(ApiError) as stale:
            await service.request_delete(owner, project_id, if_match='"v9"')
        assert stale.value.status == 412
        with pytest.raises(ApiError) as unavailable:
            await service.request_delete(owner, project_id, if_match='"v1"')
        assert (unavailable.value.code, unavailable.value.status) == (
            "capability_unavailable",
            503,
        )
        assert (await service.get(owner, project_id))["name"] == "Still here"
        with sync.connect() as connection:
            assert (
                connection.execute(
                    sa.select(sa.func.count())
                    .select_from(m.projects)
                    .where(m.projects.c.id == project_id)
                ).scalar_one()
                == 1
            )

    run(project_database, scenario)


@pytest.mark.parametrize("state", ["suspended", "deleting"])
def test_non_active_accounts_cannot_read_or_mutate_projects(
    project_database: tuple[Engine, str], state: str
) -> None:
    owner = principal(anonymous=False)

    async def scenario(
        sync: Engine, _async: AsyncEngine, _identity: IdentityService, service: ProjectsService
    ) -> None:
        _, _, project = await create(service, owner, f"blocked-{state}", "Private work")
        with sync.begin() as connection:
            connection.execute(
                sa.update(m.users)
                .where(m.users.c.firebase_uid == owner.firebase_uid)
                .values(account_state=state)
            )
        with pytest.raises(ApiError) as blocked:
            await service.get(owner, uuid.UUID(project["id"]))
        assert blocked.value.code == f"account_{state}"
        assert blocked.value.status == 403

    run(project_database, scenario)
