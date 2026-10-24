import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.core.errors import ApiError
from app.db import models as m
from app.modules.glyphs.schemas import AlternateCreate, GlyphResponse, SnapshotCreate
from app.modules.jobs.service import set_worker_fence
from app.modules.uploads.schemas import StrokeSampleCreate
from penforge_contracts import StrokeDocument
from test_glyphs_database import action, seed, select
from test_uploads_database import execute
from test_uploads_database import uploads_database as uploads_database

pytestmark = pytest.mark.integration


async def approved_pair(engine, identity, projects, storage, uploads):
    person, user, project, glyph, candidate, service = await seed(
        engine, identity, projects, storage, uploads
    )
    current = await select(service, person, project, glyph, candidate)
    first = await action(service, person, project, glyph, current, "approve")
    doc = json.loads(Path("packages/contracts/fixtures/strokes.v1.json").read_text())
    doc["strokes"][0]["points"][0]["x"] += 0.001
    _, sample = await uploads.register_sample(
        person,
        project,
        idempotency_key=uuid.uuid4().hex,
        body=StrokeSampleCreate(
            source_kind="strokes", stroke_document=StrokeDocument.model_validate(doc)
        ),
    )
    other = uuid.uuid4()
    async with engine.begin() as con:
        row = (
            (
                await con.execute(
                    sa.select(m.glyph_candidates).where(m.glyph_candidates.c.id == candidate)
                )
            )
            .mappings()
            .one()
        )
        result = (
            (
                await con.execute(
                    sa.select(m.ml_result_artifacts).where(
                        m.ml_result_artifacts.c.id == row["result_artifact_id"]
                    )
                )
            )
            .mappings()
            .one()
        )
        await set_worker_fence(
            con,
            {
                "id": result["job_id"],
                "attempts": 1,
                "lease_expires_at": datetime.now(UTC) + timedelta(seconds=60),
            },
            "fixture-worker",
        )
        await con.execute(
            m.glyph_candidates.insert().values(
                **(
                    dict(row)
                    | {
                        "id": other,
                        "source_sample_id": uuid.UUID(sample["sample_id"]),
                        "rank": 2,
                        "review_status": "pending",
                    }
                )
            )
        )
    current = await select(service, person, project, glyph, other)
    second = await action(service, person, project, glyph, current, "approve")
    return person, user, project, glyph, first, second, service


def test_authentic_alternate_explicit_conflict_safe_reversible_and_no_extra_coverage(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        person, user, project, glyph, first, second, service = await approved_pair(
            engine, identity, projects, storage, uploads
        )
        body = AlternateCreate(
            source_revision_id=first["active_revision_id"],
            base_revision_id=second["working_revision_id"],
        )
        with pytest.raises(ApiError):
            await service.add_alternate(person, project, glyph, body, '"v1"', uuid.uuid4().hex)
        key = uuid.uuid4().hex
        alternate = await service.add_alternate(
            person, project, glyph, body, f'"v{second["row_version"]}"', key
        )
        assert alternate["active_revision_id"] is None and alternate["variant_index"] == 1
        replay = await service.add_alternate(
            person, project, glyph, body, f'"v{second["row_version"]}"', key
        )
        assert GlyphResponse.model_validate(alternate) == GlyphResponse.model_validate(replay)
        approved = await action(
            service, person, project, uuid.UUID(alternate["id"]), alternate, "approve"
        )
        assert (await service.coverage(person, project))["ascii_approved"] == 1
        snapshot = await service.snapshot(
            person, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
        )
        assert len(snapshot["manifest"]["glyphs"]) == 2
        off = await service.enable_alternate(
            person, project, uuid.UUID(alternate["id"]), False, f'"v{approved["row_version"]}"'
        )
        snapoff = await service.snapshot(
            person, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
        )
        assert len(snapoff["manifest"]["glyphs"]) == 1
        await service.enable_alternate(
            person, project, uuid.UUID(alternate["id"]), True, f'"v{off["row_version"]}"'
        )
        assert (await service.get_snapshot(person, project, uuid.UUID(snapshot["id"])))[
            "manifest"
        ] == snapshot["manifest"]

    execute(uploads_database, tmp_path, scenario)
