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
SCHEMA = "phase08_browser_synthetic_20261020"
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
    cors_allowed_origins=["http://127.0.0.1:3080", "http://localhost:3080"],
    database_url=URL,
    storage_root=ROOT / ".local/phase08-browser-objects",
    hmac_secret="phase08-synthetic-local-only-acceptance-secret",
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
            temp_root=ROOT / ".local/phase08-browser-temp",
        ),
        exports=ExportWorker(
            ExportsService(engine, identity, uploads, storage),
            worker_id="privacy-export",
            temp_root=ROOT / ".local/phase08-browser-temp",
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
    from app.modules.uploads.schemas import StrokeSampleCreate
    from penforge_contracts import StrokeDocument, content_hash
    from sqlalchemy.dialects.postgresql import insert

    engine = make_async_engine(URL)
    identity = IdentityService(engine)
    storage = LocalObjectStorage(
        settings.storage_root,
        secret=hashlib.sha256(
            b"penforge-local-storage-v1\0" + settings.hmac_secret.get_secret_value().encode()
        ).digest(),
    )
    uploads, _ = capture_services(settings, engine, identity, storage)
    try:
        async with engine.connect() as con:
            user = (
                (
                    await con.execute(
                        sa.select(m.users).order_by(m.users.c.created_at.desc()).limit(1)
                    )
                )
                .mappings()
                .one()
            )
        now = int(time.time())
        person = Principal(
            user_id=user["id"],
            firebase_uid=user["firebase_uid"],
            is_anonymous=user["is_anonymous"],
            sign_in_provider="anonymous" if user["is_anonymous"] else "password",
            email_verified=False,
            auth_time=now,
            token_issued_at=now,
        )
        projects = ProjectsService(engine, identity, cursor_secret=b"f" * 32)
        _, _, project = await projects.create(
            person,
            idempotency_key="phase08-synthetic-fixture",
            name="Phase 08 synthetic geometry",
            script_code="Latn",
            style_controls={},
        )
        pid = project["id"]
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
        source = json.loads((ROOT / "packages/contracts/fixtures/outline.v1.json").read_text())
        document = json.loads((ROOT / "packages/contracts/fixtures/strokes.v1.json").read_text())
        identifiers = {}
        for cp in range(32, 127):
            gid = uuid4()
            variants = 3 if cp in (101, 108) else 1
            history = []
            for index in range(variants):
                sid = None
                if cp != 32:
                    doc = json.loads(json.dumps(document))
                    doc["strokes"][0]["points"][0]["x"] += (cp * 3 + index) * 0.00001
                    _, sample = await uploads.register_sample(
                        person,
                        pid,
                        idempotency_key=f"fixture-{cp}-{index}",
                        body=StrokeSampleCreate(
                            source_kind="strokes",
                            stroke_document=StrokeDocument.model_validate(doc),
                        ),
                    )
                    from uuid import UUID

                    sid = UUID(sample["sample_id"])
                outline = json.loads(json.dumps(source))
                outline["codepoints"] = [cp]
                if cp == 32:
                    outline["contours"] = []
                    outline["metrics"] = {"advance_width": 250.0, "left_side_bearing": 0.0}
                elif index:
                    for contour in outline["contours"]:
                        contour["start"]["x"] += index * 20
                        for segment in contour["segments"]:
                            for name in ("to", "c", "c1", "c2"):
                                if name in segment:
                                    segment[name]["x"] += index * 20
                rid = uuid4()
                async with engine.begin() as con:
                    if index == 0:
                        await con.execute(
                            m.glyphs.insert().values(id=gid, project_id=pid, codepoints=[cp])
                        )
                    await con.execute(
                        m.glyph_revisions.insert().values(
                            id=rid,
                            glyph_id=gid,
                            seq=index + 1,
                            parent_revision_id=history[-1] if history else None,
                            origin="user_capture" if sid else "user_edit",
                            approval="approved",
                            outline_inline=outline,
                            metrics=outline["metrics"],
                            content_hash=bytes.fromhex(content_hash(outline)[7:]),
                            source_sample_id=sid,
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
                history.append(rid)
            identifiers[str(cp)] = {"glyph_id": str(gid), "revisions": [str(v) for v in history]}
        folder = ROOT / "artifacts/phase08/browser"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "fixture.json").write_text(
            json.dumps(
                {
                    "project_id": str(pid),
                    "glyphs": identifiers,
                    "domain": "synthetic_engineering_fixture",
                },
                indent=2,
            )
        )
    finally:
        storage.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["initialize", "seed", "worker"])
    mode = parser.parse_args().mode
    if mode == "initialize":
        initialize()
    elif mode == "seed":
        asyncio.run(seed())
    else:
        asyncio.run(worker())
