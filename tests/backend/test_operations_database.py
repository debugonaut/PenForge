"""Exercise repaired operator procedures against the migrated PostgreSQL schema."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from app.core.errors import ApiError
from app.core.operational_metrics import collect
from app.db import models as m
from app.modules.jobs import repository
from app.modules.jobs.quota_config import QuotaConfiguration
from sqlalchemy.ext.asyncio import create_async_engine
from test_uploads_database import (  # noqa: F401
    body,
    create_project,
    execute,
    principal,
)
from test_uploads_database import uploads_database as operations_database  # noqa: F401

from scripts.configure_quotas import provision
from scripts.phase11_pg_benchmark import seed

pytestmark = pytest.mark.integration


def example():
    return QuotaConfiguration.model_validate(
        json.loads(Path("infrastructure/deployment/quota-tiers.example.json").read_text())
    )


@pytest.mark.parametrize("guest", [False, True])
def test_quota_setup_is_idempotent_and_admits_real_upload_and_compute(
    operations_database,  # noqa: F811
    tmp_path,
    guest,
):
    config = example()
    sync, _ = operations_database
    with sync.begin() as con:
        provision(con, config)
        provision(con, config)
        changed = config.model_copy(deep=True)
        changed.global_limits["storage_bytes"].limit_amount += 1
        with pytest.raises(ValueError, match="different_limits"):
            provision(con, changed)

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        actor = principal(anonymous=guest)
        owner, project = await create_project(identity, projects, actor)
        status, response = await uploads.initialize(
            actor, project, idempotency_key=uuid4().hex, body=body()
        )
        assert status is False and response["asset_id"]
        async with engine.begin() as con:
            await repository.reserve(con, owner, None, {"compute_seconds": 60}, datetime.now(UTC))
        async with engine.begin() as con:
            with pytest.raises(ApiError) as exc:
                await repository.reserve(
                    con, owner, None, {"compute_seconds": 3601}, datetime.now(UTC)
                )
            assert exc.value.code == "quota_exceeded"
        async with engine.connect() as con:
            rows = (await con.execute(sa.select(m.quota_balances))).mappings().all()
            assert {r["resource"] for r in rows} == {"storage_bytes", "compute_seconds"}
            assert sum(r["reserved"] for r in rows if r["resource"] == "compute_seconds") == 120

    execute(operations_database, tmp_path, scenario)


def test_metrics_use_live_database_and_only_current_quota_windows(operations_database):  # noqa: F811
    sync, schema = operations_database
    seed(sync.url.render_as_string(hide_password=False), schema, 1, 2)
    with sync.begin() as con:
        owner = con.scalar(sa.select(m.users.c.id))
        today = con.scalar(sa.text("SELECT (now() AT TIME ZONE 'UTC')::date"))
        con.execute(
            m.quota_balances.insert(),
            [
                dict(
                    id=uuid4(),
                    scope="global",
                    resource="storage_bytes",
                    window_kind="day",
                    window_start=today - timedelta(days=offset),
                    limit_amount=1000,
                    committed=100 if offset == 0 else 999,
                )
                for offset in (0, 1)
            ],
        )
        project = con.scalar(sa.select(m.projects.c.id))
        for state, size in (("ready", 123), ("pending_upload", 999)):
            con.execute(
                m.assets.insert().values(
                    id=uuid4(),
                    owner_user_id=owner,
                    project_id=project,
                    kind="original_upload",
                    storage_backend="local_fs",
                    storage_key=uuid4().hex,
                    mime_type="image/png",
                    size_bytes=size,
                    sha256=b"b" * 32 if state == "ready" else None,
                    state=state,
                    verified_at=datetime.now(UTC) if state == "ready" else None,
                    retention_class="original",
                )
            )
        con.execute(
            m.deletion_requests.insert().values(
                id=uuid4(),
                user_id=owner,
                subject_ref=b"a" * 32,
                scope="contributions_only",
                state="failed",
            )
        )

    async def run():
        engine = create_async_engine(sync.url, connect_args={"options": f"-csearch_path={schema}"})
        try:
            async with engine.connect() as con:
                text = "\n".join(await collect(con))
                assert "penforge_scrape_db_ok 1" in text
                assert 'penforge_queue_depth{type="compile_export",state="queued"} 2' in text
                assert (
                    'penforge_quota_storage_bytes_committed{scope="global",resource="storage_bytes"} 100'
                    in text
                )
                assert "penforge_failed_deletions 1.000" in text
                assert "penforge_stored_asset_bytes 123.000" in text
                assert str(owner) not in text
        finally:
            await engine.dispose()

    asyncio.run(run())

    # Exercise the actual protected HTTP scrape, not only the SQL collector.
    from types import SimpleNamespace

    from app.core.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from sqlalchemy.pool import NullPool

    http_engine = create_async_engine(
        sync.url, poolclass=NullPool, connect_args={"options": f"-csearch_path={schema}"}
    )
    token = "m" * 40
    app = create_app(
        Settings(
            app_env="test",
            auth_backend="disabled",
            storage_backend="disabled",
            readiness_token=SecretStr(token),
        )
    )
    app.state.identity_service = SimpleNamespace(engine=http_engine)
    try:
        with TestClient(app) as client:
            response = client.get("/internal/metrics", headers={"Authorization": "Bearer " + token})
            assert response.status_code == 200
            assert "penforge_scrape_db_ok 1" in response.text
            assert "penforge_failed_deletions 1.000" in response.text
    finally:
        asyncio.run(http_engine.dispose())


@pytest.mark.parametrize("scope", ["account", "project", "contributions_only"])
def test_api_startup_and_worker_reconcile_keep_pre_rotation_receipts(
    operations_database,  # noqa: F811
    tmp_path,
    scope,
):
    from app.core.config import Settings
    from app.main import create_app
    from app.modules.privacy.schemas import DeletionCreate
    from app.modules.privacy.service import PrivacyService
    from pydantic import SecretStr

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        actor = principal()
        _, project = await create_project(identity, projects, actor)
        original = Settings(hmac_secret=SecretStr("a" * 40))
        service = PrivacyService(engine, identity, uploads, storage, original.privacy_key)
        receipt = await service.create(
            actor,
            DeletionCreate(
                scope=scope, confirmed=True, project_id=project if scope == "project" else None
            ),
            uuid4().hex,
        )
        rotated = Settings(
            app_env="test",
            auth_backend="disabled",
            storage_backend="disabled",
            hmac_secret=SecretStr("b" * 40),
            privacy_hmac_secret=SecretStr("a" * 40),
        )
        app = create_app(rotated, identity_service=identity, uploads_service=uploads)
        async with app.router.lifespan_context(app):
            status = await app.state.privacy_service.get(
                None, receipt["id"], receipt["progress_token"]
            )
            assert status["scope"] == scope
        # The worker uses this same stable key and reconciliation path on startup.
        worker = PrivacyService(engine, identity, uploads, storage, rotated.privacy_key)
        await worker.restore_reconcile()
        assert (await worker.get(None, receipt["id"], receipt["progress_token"]))["scope"] == scope
        assert worker.journal.entries() == service.journal.entries()

    execute(operations_database, tmp_path, scenario)
