"""Disposable synthetic Phase 05 browser fixture. No provider or real writing."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.adapters.auth.base import Principal
from app.adapters.storage.local_fs import LocalObjectStorage
from app.core.config import Settings
from app.db import models as m
from app.main import create_app
from app.modules.exports.service import ExportsService
from app.modules.glyphs.schemas import SnapshotCreate
from app.modules.glyphs.service import GlyphsService
from app.modules.identity.service import IdentityService
from app.modules.projects.service import ProjectsService
from app.modules.uploads.factory import capture_services
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import create_async_engine

from services.worker.exports import ExportWorker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "phase05_browser_synthetic_20261020"
URL = "postgresql+psycopg://penforge@/penforge_test?host=/private/tmp/penforge-pg-501&port=55432"
settings = Settings(
    app_env="local",
    auth_backend="firebase",
    firebase_project_id="demo-penforge-identity",
    firebase_allow_emulator=True,
    firebase_auth_emulator_host="127.0.0.1:9099",
    cors_allowed_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3050",
    ],
    storage_root=ROOT / ".local/phase05-browser-objects",
    hmac_secret="phase05-local-synthetic-only-acceptance-secret",
    identity_rate_limit=10000,
)


def engine():
    return create_async_engine(URL, connect_args={"options": f"-csearch_path={SCHEMA}"})


def app():
    db = engine()
    identity = IdentityService(db)
    if os.environ.get("PENFORGE_ACCEPTANCE_EXPIRED_FIRST_DOWNLOAD") != "1":
        return create_app(settings, identity_service=identity)
    storage = LocalObjectStorage(
        settings.storage_root,
        secret=hashlib.sha256(
            b"penforge-local-storage-v1\0" + settings.hmac_secret.get_secret_value().encode()
        ).digest(),
    )
    uploads, _ = capture_services(settings, db, identity, storage)
    uploads.signed_download_ttl_seconds = 1

    class ExpiredOnceExports(ExportsService):
        expired_once = False

        async def download(self, principal, export_id):
            grant = await super().download(principal, export_id)
            if not self.expired_once:
                self.expired_once = True
                await asyncio.sleep(2)
                print("phase05_acceptance_expired_first_capability", flush=True)
            return grant

    service = ExpiredOnceExports(db, identity, uploads, storage)
    return create_app(
        settings, identity_service=identity, uploads_service=uploads, exports_service=service
    )


def initialize():
    sync = sa.create_engine(URL)
    with sync.begin() as con:
        con.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
    sync.dispose()
    sync = sa.create_engine(URL, connect_args={"options": f"-csearch_path={SCHEMA}"})
    with sync.begin() as con:
        config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
        config.attributes["connection"] = con
        command.upgrade(config, "head")
    sync.dispose()


async def run(mode):
    db = engine()
    identity = IdentityService(db)
    storage = LocalObjectStorage(
        settings.storage_root,
        secret=hashlib.sha256(
            b"penforge-local-storage-v1\0" + settings.hmac_secret.get_secret_value().encode()
        ).digest(),
    )
    uploads, _ = capture_services(settings, db, identity, storage)
    service = ExportsService(db, identity, uploads, storage)
    try:
        if mode == "worker":
            worker = ExportWorker(
                service,
                worker_id="phase05-browser",
                temp_root=ROOT / ".local/phase05-browser-temp",
                heartbeat_seconds=0.2,
            )
            end = time.monotonic() + 1800
            while time.monotonic() < end:
                if not await worker.run_once():
                    await asyncio.sleep(0.5)
            return
        async with db.connect() as con:
            user = (
                (
                    await con.execute(
                        sa.select(m.users)
                        .where(m.users.c.merged_into_user_id.is_(None))
                        .order_by(m.users.c.created_at.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one()
            )
        now = int(time.time())
        owner = Principal(
            user_id=user["id"],
            firebase_uid=user["firebase_uid"],
            is_anonymous=user["is_anonymous"],
            sign_in_provider="anonymous",
            email_verified=False,
            auth_time=now,
            token_issued_at=now,
        )
        projects = ProjectsService(db, identity, cursor_secret=b"f" * 32)
        _, _, project = await projects.create(
            owner,
            idempotency_key=uuid4().hex,
            name="Phase 05 synthetic geometry",
            script_code="Latn",
            style_controls={},
        )
        from datetime import date

        from penforge_contracts import content_hash

        source = json.loads((ROOT / "packages/contracts/fixtures/outline.v1.json").read_text())
        async with db.begin() as con:
            for resource, amount in [
                ("storage_bytes", 100_000_000),
                ("exports", 1000),
                ("compute_seconds", 100_000),
            ]:
                for scope in ["user", "global"]:
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
            for cp in list(range(65, 91)) + list(range(97, 123)):
                outline = json.loads(json.dumps(source))
                outline["codepoints"] = [cp]
                gid, rid = uuid4(), uuid4()
                await con.execute(
                    m.glyphs.insert().values(id=gid, project_id=project["id"], codepoints=[cp])
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
        snap = await GlyphsService(db, identity, storage).snapshot(
            owner,
            project["id"],
            body=SnapshotCreate(label="Synthetic Phase 05 only"),
            idempotency_key=uuid4().hex,
        )
        (ROOT / "artifacts/phase05-browser/fixture.json").write_text(
            json.dumps(
                {"project_id": project["id"], "snapshot_id": snap["id"], "synthetic": True},
                default=str,
                indent=2,
            )
            + "\n"
        )
        print(
            "Disposable approved synthetic geometry seeded; no Phase 04 browser approval claimed."
        )
    finally:
        storage.close()
        await db.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["initialize", "seed", "worker"])
    args = parser.parse_args()
    if args.mode == "initialize":
        initialize()
    else:
        asyncio.run(run(args.mode))
