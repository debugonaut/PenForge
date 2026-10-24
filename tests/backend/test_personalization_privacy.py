import uuid

import pytest
import sqlalchemy as sa
from app.core.errors import ApiError
from app.db import models as m
from app.modules.glyphs.schemas import SnapshotCreate
from app.modules.privacy.schemas import ConsentPut, DeletionCreate
from test_glyphs_database import action, seed, select
from test_privacy_database import Admin, drain, privacy, seed_asset
from test_uploads_database import execute
from test_uploads_database import uploads_database as uploads_database

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("scope", ["project", "account"])
def test_phase08_private_artifacts_erased_and_stale_profile_publication_denied(
    uploads_database, tmp_path, scope
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person, user, project, glyph, candidate, glyphs = await seed(
            engine, identity, projects, storage, uploads
        )
        selected = await select(glyphs, person, project, glyph, candidate)
        await action(glyphs, person, project, glyph, selected, "approve")
        snapshot = await glyphs.snapshot(
            person, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
        )
        await glyphs.measure_style(person, project, uuid.UUID(snapshot["id"]), uuid.uuid4().hex)
        await seed_asset(engine, storage, user, project, kind="style_embedding")
        admin = Admin(engine, [user])
        service = privacy(engine, identity, uploads, storage, admin)
        # Training withdrawal removes contribution copies, not the personal font.
        await service.put_consent(
            person,
            ConsentPut(purpose="model_training", granted=True, policy_version="model-training-v1"),
        )
        withdrawal = await service.put_consent(
            person,
            ConsentPut(purpose="model_training", granted=False, policy_version="model-training-v1"),
        )
        req = await service.get(person, withdrawal["withdrawal_deletion_id"])
        await drain(service, req)
        assert (await glyphs.style_profile(person, project))["version"] == 1
        async with engine.begin() as con:
            # Fixture's analysis job holds no real process. Expire it for the saga.
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.project_id == project, m.jobs.c.state == "running")
                .values(lease_expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'"))
            )
        deletion = await service.create(
            person,
            DeletionCreate(
                scope=scope, project_id=project if scope == "project" else None, confirmed=True
            ),
            "phase08-erase",
        )
        with pytest.raises(ApiError):
            await glyphs.measure_style(person, project, uuid.UUID(snapshot["id"]), uuid.uuid4().hex)
        await drain(service, deletion)
        async with engine.connect() as con:
            for table in (m.style_profiles, m.glyph_candidates, m.project_snapshots, m.assets):
                assert await con.scalar(sa.select(sa.func.count()).select_from(table)) == 0
        assert not [o for o in storage.list_objects() if not o.key.startswith("privacy/")]
        with pytest.raises(ApiError):
            await glyphs.measure_style(person, project, uuid.UUID(snapshot["id"]), uuid.uuid4().hex)

    execute(uploads_database, tmp_path, scenario)
