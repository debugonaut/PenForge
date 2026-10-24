import uuid

import pytest
import sqlalchemy as sa
from app.db import models as m
from app.modules.glyphs.schemas import SnapshotCreate
from app.modules.privacy.schemas import DeletionCreate
from penforge_contracts import GlyphOutline, content_hash
from test_database import migrate
from test_glyphs_database import action, seed, select
from test_privacy_database import drain, privacy, seed_asset
from test_uploads_database import execute
from test_uploads_database import uploads_database as uploads_database

pytestmark = pytest.mark.integration


def test_phase08_seeded_profile_and_source_survive_phase09_up_down_up(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        pending = await select(service, person, project, glyph, candidate)
        await action(service, person, project, glyph, pending, "approve")
        snap = await service.snapshot(
            person, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
        )
        profile = await service.measure_style(
            person, project, uuid.UUID(snap["id"]), uuid.uuid4().hex
        )
        objects = {
            o.key: storage.read_object(o.key, max_bytes=10_000_000) for o in storage.list_objects()
        }
        migrate(sync, "downgrade", "0011_personalization")
        migrate(sync, "upgrade", "head")
        assert await service.style_profile(person, project) == profile
        assert objects == {
            o.key: storage.read_object(o.key, max_bytes=10_000_000) for o in storage.list_objects()
        }
        assert (await service.get(person, project, glyph))["active_revision_id"] is not None

    execute(uploads_database, tmp_path, scenario)


def test_deva_anchors_forms_and_artifacts_are_erased_and_late_edits_denied(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads, point=0x915
        )
        async with engine.begin() as con:
            await con.execute(
                m.projects.update().where(m.projects.c.id == project).values(script_code="Deva")
            )
        pending = await select(service, person, project, glyph, candidate)
        approved = await action(service, person, project, glyph, pending, "approve")
        o = dict(approved["outline"])
        o["indic"] = dict(
            schema_version=1,
            role="base",
            source_classification="owner_engineering_record",
            geometry_hash=content_hash(o),
            anchors={"above_base": dict(x=250.0, y=700.0), "below_base": dict(x=250.0, y=0.0)},
            substitution=None,
        )
        changed = await action(
            service,
            person,
            project,
            glyph,
            approved,
            "edit",
            outline=GlyphOutline.model_validate(o),
        )
        await action(service, person, project, glyph, changed, "approve")
        snap = await service.snapshot(
            person, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
        )
        assert snap["manifest"]["required_set_id"] == "deva-tier1-ucd17-v1"
        for kind in ("vector", "segment", "validation_report"):
            await seed_asset(engine, storage, user, project, kind=kind)
        async with engine.begin() as con:
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.project_id == project, m.jobs.c.state == "running")
                .values(lease_expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'"))
            )
        privacy_service = privacy(engine, identity, uploads, storage)
        request = await privacy_service.create(
            person,
            DeletionCreate(scope="project", project_id=project, confirmed=True),
            "deva-erasure",
        )
        from app.core.errors import ApiError

        with pytest.raises(ApiError):
            await service.get(person, project, glyph)
        assert (await drain(privacy_service, request))["state"] == "completed"
        async with engine.connect() as con:
            for table in (
                m.projects,
                m.glyphs,
                m.glyph_revisions,
                m.assets,
                m.samples,
                m.project_snapshots,
                m.glyph_candidates,
            ):
                assert await con.scalar(sa.select(sa.func.count()).select_from(table)) == 0
        assert not [o for o in storage.list_objects() if not o.key.startswith("privacy/")]

    execute(uploads_database, tmp_path, scenario)
