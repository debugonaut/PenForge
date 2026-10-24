import hashlib
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.db import models as m
from app.modules.privacy.schemas import DeletionCreate
from test_database import migrate
from test_privacy_database import drain, privacy
from test_uploads_database import create_project, execute, principal
from test_uploads_database import uploads_database as uploads_database

pytestmark = pytest.mark.integration


def test_previous_privacy_release_seed_and_erasure_journal_survive_upgrade(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person = principal()
        user, project = await create_project(identity, projects, person)
        service = privacy(engine, identity, uploads, storage)
        request = await service.create(
            person,
            DeletionCreate(scope="project", project_id=project, confirmed=True),
            "previous-release",
        )
        async with engine.connect() as con:
            before = dict((await con.execute(sa.select(m.deletion_requests))).mappings().one())
        journal = {
            o.key: hashlib.sha256(storage.read_object(o.key, max_bytes=100000)).hexdigest()
            for o in storage.list_objects()
        }
        migrate(sync, "downgrade", "0010_privacy")
        migrate(sync, "upgrade", "head")
        async with engine.connect() as con:
            after = dict((await con.execute(sa.select(m.deletion_requests))).mappings().one())
            assert before == after
            assert (
                await con.scalar(sa.text("SELECT version_num FROM alembic_version"))
                == "0013_account_workspace"
            )
        assert journal == {
            o.key: hashlib.sha256(storage.read_object(o.key, max_bytes=100000)).hexdigest()
            for o in storage.list_objects()
        }
        assert (await drain(service, request))["state"] == "completed"
        out = Path("artifacts/phase09/regressions")
        out.mkdir(parents=True, exist_ok=True)
        (out / "previous-release-migration.json").write_text(
            json.dumps(
                {
                    "previous_head": "0010_privacy",
                    "head": "0013_account_workspace",
                    "privacy_request_unchanged": True,
                    "journal_byte_hashes_unchanged": True,
                    "erasure_resumed": True,
                }
            )
        )

    execute(uploads_database, tmp_path, scenario)
