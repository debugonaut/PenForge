import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.core.errors import ApiError
from app.db import models as m
from app.modules.glyphs.schemas import SnapshotCreate, SnapshotResponse
from app.modules.glyphs.style_measurements import measure
from sqlalchemy.exc import IntegrityError
from test_glyphs_database import action, seed, select
from test_uploads_database import execute, principal
from test_uploads_database import uploads_database as uploads_database


def test_style_bytes_are_deterministic_and_unknowns_stay_null():
    raw = json.loads(Path("packages/contracts/fixtures/outline.v1.json").read_text())
    refs = [{"outline": raw, "codepoints": [65], "variant_index": 0, "revision_hash": "a"}]
    a = measure("snapshot", refs)
    assert a == measure("snapshot", list(reversed(refs)))
    assert a[0]["summary"]["writing_rhythm"] is None
    assert a[0]["summary"]["pressure"] is None
    assert len(a[1]) == 71


@pytest.mark.integration
def test_profile_snapshot_owner_approval_identity_and_immutability(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        current = await select(service, owner, project, glyph, candidate)
        empty = await service.snapshot(
            owner, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
        )
        with pytest.raises(ApiError):
            await service.measure_style(owner, project, uuid.UUID(empty["id"]), uuid.uuid4().hex)
        await action(service, owner, project, glyph, current, "approve")
        snap = await service.snapshot(
            owner, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
        )
        key = uuid.uuid4().hex
        profile = await service.measure_style(owner, project, uuid.UUID(snap["id"]), key)
        again = await service.measure_style(owner, project, uuid.UUID(snap["id"]), uuid.uuid4().hex)
        assert (
            profile
            == again
            == await service.measure_style(owner, project, uuid.UUID(snap["id"]), key)
        )
        assert profile["version"] == 1
        with pytest.raises(ApiError):
            await service.measure_style(
                principal(), project, uuid.UUID(snap["id"]), uuid.uuid4().hex
            )
        with pytest.raises(IntegrityError):
            async with engine.begin() as con:
                await con.execute(m.style_profiles.update().values(metrics={}))
        async with engine.begin() as con:
            await con.execute(
                m.projects.update().where(m.projects.c.id == project).values(status="deleting")
            )
        with pytest.raises(ApiError):
            await service.measure_style(owner, project, uuid.UUID(snap["id"]), uuid.uuid4().hex)
        with pytest.raises(IntegrityError):
            async with engine.begin() as con:
                await con.execute(
                    m.style_profiles.insert().values(
                        id=uuid.uuid4(),
                        project_id=project,
                        version=2,
                        metrics=profile["metrics"],
                        source_snapshot_id=uuid.UUID(snap["id"]),
                        profile_sha256=b"x" * 32,
                        algorithm_version="stale",
                        created_by_user_id=user,
                    )
                )
        async with engine.begin() as con:
            await con.execute(m.projects.delete().where(m.projects.c.id == project))
        async with engine.connect() as con:
            assert await con.scalar(sa.select(sa.func.count()).select_from(m.style_profiles)) == 0

    execute(uploads_database, tmp_path, scenario)


@pytest.mark.integration
def test_approved_synthetic_is_not_authentic_profile_input(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads, origin="style_conditioned_generation"
        )
        current = await select(service, owner, project, glyph, candidate)
        await action(service, owner, project, glyph, current, "approve")
        snap = await service.snapshot(
            owner, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
        )
        with pytest.raises(ApiError):
            await service.measure_style(owner, project, uuid.UUID(snap["id"]), uuid.uuid4().hex)

    execute(uploads_database, tmp_path, scenario)


@pytest.mark.integration
def test_large_historical_style_snapshot_receipt_is_compact_and_replayable(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        current = await select(service, owner, project, glyph, candidate)
        await action(service, owner, project, glyph, current, "approve")
        metrics = {"fixture_observations": list(range(10000))}
        async with engine.begin() as con:
            await con.execute(
                m.style_profiles.insert().values(
                    id=uuid.uuid4(), project_id=project, version=1, metrics=metrics
                )
            )
        key = uuid.uuid4().hex
        first = await service.snapshot(
            owner, project, body=SnapshotCreate(label="Large style"), idempotency_key=key
        )
        again = await service.snapshot(
            owner, project, body=SnapshotCreate(label="Large style"), idempotency_key=key
        )
        assert SnapshotResponse.model_validate(first) == SnapshotResponse.model_validate(again)
        assert first["manifest"]["style_profile_snapshot"]["metrics"] == metrics
        async with engine.connect() as con:
            receipt = await con.scalar(
                sa.select(m.idempotency_keys.c.response_body).where(m.idempotency_keys.c.key == key)
            )
            assert len(json.dumps(receipt)) < 300
        assert len(json.dumps(first["manifest"])) > 32768

    execute(uploads_database, tmp_path, scenario)
