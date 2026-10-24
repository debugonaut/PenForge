"""Isolated synthetic personalization acceptance server/worker; no hosted or user data."""

import argparse
import asyncio
import hashlib
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.adapters.auth.firebase import FirebaseAuthVerifier
from app.adapters.storage.local_fs import LocalObjectStorage
from app.core.config import Settings
from app.db.session import make_async_engine
from app.main import create_app
from app.modules.analysis.service import AnalysisService
from app.modules.exports.service import ExportsService
from app.modules.identity.service import IdentityService
from app.modules.privacy.service import PrivacyService
from app.modules.uploads.factory import capture_services

from services.worker.analysis import AnalysisWorker
from services.worker.exports import ExportWorker
from services.worker.runtime import PipelineWorker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "phase09_browser_synthetic_20261020"
BASE = "postgresql+psycopg://penforge@/penforge_test?host=/private/tmp/penforge-pg-501&port=55432"
URL = (
    sa.make_url(BASE)
    .update_query_dict({"options": f"-csearch_path={SCHEMA}"})
    .render_as_string(hide_password=False)
)
settings = Settings(
    app_env="local",
    auth_backend="firebase",
    firebase_project_id="demo-penforge-identity",
    firebase_allow_emulator=True,
    firebase_auth_emulator_host="127.0.0.1:9099",
    cors_allowed_origins=["http://127.0.0.1:3090", "http://localhost:3090"],
    database_url=URL,
    storage_root=ROOT / ".local/phase09-browser-objects",
    hmac_secret="phase09-synthetic-local-only-acceptance-secret",
    identity_rate_limit=10000,
)


def app():
    return create_app(settings)


def initialize():
    engine = sa.create_engine(BASE)
    with engine.begin() as con:
        con.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
    engine.dispose()
    engine = sa.create_engine(URL)
    with engine.begin() as con:
        config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
        config.attributes["connection"] = con
        command.upgrade(config, "head")
    engine.dispose()


async def worker():
    engine = make_async_engine(URL)
    identity = IdentityService(engine)
    storage = LocalObjectStorage(
        settings.storage_root,
        secret=hashlib.sha256(
            b"penforge-local-storage-v1\0" + settings.hmac_secret.get_secret_value().encode()
        ).digest(),
    )
    uploads, queue = capture_services(settings, engine, identity, storage)
    admin = FirebaseAuthVerifier(settings)
    privacy = PrivacyService(
        engine,
        identity,
        uploads,
        storage,
        settings.hmac_secret.get_secret_value().encode(),
        identity_admin=admin,
    )
    await privacy.restore_reconcile()
    runner = PipelineWorker(
        queue,
        uploads,
        storage,
        worker_id="privacy-browser",
        privacy=privacy,
        analysis=AnalysisWorker(
            AnalysisService(engine, identity, uploads, storage),
            worker_id="privacy-analysis",
            temp_root=ROOT / ".local/phase09-browser-temp",
        ),
        exports=ExportWorker(
            ExportsService(engine, identity, uploads, storage),
            worker_id="privacy-export",
            temp_root=ROOT / ".local/phase09-browser-temp",
        ),
    )
    try:
        await runner.run(mode="persistent", max_seconds=3600)
    finally:
        admin.close()
        storage.close()
        await engine.dispose()


async def seed():
    import json
    import time
    from datetime import date
    from uuid import uuid4

    from app.adapters.auth.base import Principal
    from app.db import models as m
    from app.modules.projects.service import ProjectsService
    from penforge_contracts import content_hash
    from sqlalchemy.dialects.postgresql import insert

    from scripts.deva_fixture import fixture

    engine = make_async_engine(URL)
    identity = IdentityService(engine)
    async with engine.connect() as con:
        user = (
            (await con.execute(sa.select(m.users).order_by(m.users.c.created_at.desc()).limit(1)))
            .mappings()
            .one()
        )
    now = int(time.time())
    person = Principal(
        user_id=user["id"],
        firebase_uid=user["firebase_uid"],
        is_anonymous=True,
        sign_in_provider="anonymous",
        email_verified=False,
        auth_time=now,
        token_issued_at=now,
    )
    projects = ProjectsService(engine, identity, cursor_secret=b"f" * 32)
    _, _, project = await projects.create(
        person,
        idempotency_key="phase09-reference-fixture",
        name="Devanagari engineering fixture (Noto, not handwriting)",
        script_code="Deva",
        style_controls={},
    )
    _, rows = fixture()
    async with engine.begin() as con:
        for resource, amount in [
            ("storage_bytes", 100000000),
            ("exports", 1000),
            ("compute_seconds", 100000),
        ]:
            for scope in ("user", "global"):
                await con.execute(
                    insert(m.quota_balances)
                    .values(
                        id=uuid4(),
                        scope=scope,
                        user_id=user["id"] if scope == "user" else None,
                        resource=resource,
                        window_kind="lifetime",
                        window_start=date(1970, 1, 1),
                        limit_amount=amount,
                    )
                    .on_conflict_do_nothing()
                )
        for row in rows:
            gid, rid = uuid4(), uuid4()
            o = row["outline"]
            await con.execute(
                m.glyphs.insert().values(
                    id=gid, project_id=project["id"], codepoints=o["codepoints"]
                )
            )
            await con.execute(
                m.glyph_revisions.insert().values(
                    id=rid,
                    glyph_id=gid,
                    seq=1,
                    origin="user_edit",
                    approval="approved",
                    outline_inline=o,
                    metrics=o["metrics"],
                    content_hash=bytes.fromhex(content_hash(o)[7:]),
                    created_by_user_id=user["id"],
                    approved_by_user_id=user["id"],
                    approved_at=sa.func.now(),
                )
            )
            await con.execute(
                m.glyphs.update()
                .where(m.glyphs.c.id == gid)
                .values(active_revision_id=rid, coverage_status="approved")
            )
    out = ROOT / "artifacts/phase09/browser"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fixture.json").write_text(
        json.dumps(
            dict(project_id=project["id"], domain="reference_font_engineering_not_handwriting"),
            indent=2,
        )
    )
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["initialize", "seed", "worker"])
    mode = parser.parse_args().mode
    if mode == "initialize":
        initialize()
    else:
        asyncio.run(seed() if mode == "seed" else worker())
