"""Isolated synthetic privacy acceptance server/worker; no hosted or user data."""

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
SCHEMA = "phase07_browser_synthetic_20261020"
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
    cors_allowed_origins=["http://127.0.0.1:3070", "http://localhost:3070"],
    database_url=URL,
    storage_root=ROOT / ".local/phase07-browser-objects",
    hmac_secret="phase07-synthetic-local-only-acceptance-secret",
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
            temp_root=ROOT / ".local/phase07-browser-temp",
        ),
        exports=ExportWorker(
            ExportsService(engine, identity, uploads, storage),
            worker_id="privacy-export",
            temp_root=ROOT / ".local/phase07-browser-temp",
        ),
    )
    try:
        await runner.run(mode="persistent", max_seconds=3600)
    finally:
        admin.close()
        storage.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["initialize", "worker"])
    if parser.parse_args().mode == "initialize":
        initialize()
    else:
        asyncio.run(worker())
