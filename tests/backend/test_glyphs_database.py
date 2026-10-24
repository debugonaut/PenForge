"""Real PostgreSQL review/approval, immutable snapshots and coverage acceptance."""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.core.errors import ApiError
from app.db import models as m
from app.modules.glyphs.schemas import GlyphPatch, SelectCandidate, SnapshotCreate
from app.modules.glyphs.service import ASCII, CORE, GlyphsService
from app.modules.jobs.service import set_worker_fence
from app.modules.uploads.schemas import StrokeSampleCreate
from penforge_contracts import GlyphOutline, StrokeDocument, content_hash
from sqlalchemy.exc import IntegrityError
from test_uploads_database import configure, create_project, execute, principal
from test_uploads_database import uploads_database as uploads_database

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def outline(point=65, width=520.0):
    value = json.loads((ROOT / "packages/contracts/fixtures/outline.v1.json").read_text())
    value["codepoints"] = [point]
    value["metrics"]["advance_width"] = width
    return GlyphOutline.model_validate(value).model_dump(mode="json")


async def seed(
    engine,
    identity,
    projects,
    storage,
    uploads,
    *,
    origin="authentic_extraction",
    point=65,
    owner=None,
):
    owner = owner or principal()
    user, project = await create_project(identity, projects, owner)
    await configure(engine, user)
    document = StrokeDocument.model_validate_json(
        (ROOT / "packages/contracts/fixtures/strokes.v1.json").read_bytes()
    )
    _, sample = await uploads.register_sample(
        owner,
        project,
        idempotency_key=uuid.uuid4().hex,
        body=StrokeSampleCreate(source_kind="strokes", stroke_document=document),
    )
    job, result, manifest_asset, glyph, candidate = [uuid.uuid4() for _ in range(5)]
    geometry = outline(point)
    data = b'{"immutable":"synthetic review fixture"}'
    digest = hashlib.sha256(data).digest()
    key = f"u/{user}/p/{project}/results/{manifest_asset}.json"
    storage.put_immutable_stream(key, [data], size_bytes=len(data), sha256=digest.hex())
    now = datetime.now(UTC)
    async with engine.begin() as connection:
        await connection.execute(
            m.assets.insert().values(
                id=manifest_asset,
                owner_user_id=user,
                project_id=project,
                kind="validation_report",
                storage_backend="local_fs",
                storage_key=key,
                mime_type="application/json",
                size_bytes=len(data),
                sha256=digest,
                state="ready",
                retention_class="vector",
                verified_at=now,
            )
        )
        await connection.execute(
            m.jobs.insert().values(
                id=job,
                owner_user_id=user,
                project_id=project,
                job_type="analyze_samples",
                payload={},
                payload_schema_version=1,
                attempts=1,
                state="running",
                lease_holder="fixture-worker",
                lease_expires_at=now + timedelta(seconds=60),
            )
        )
        await set_worker_fence(
            connection,
            {"id": job, "attempts": 1, "lease_expires_at": now + timedelta(seconds=60)},
            "fixture-worker",
        )
        await connection.execute(
            m.ml_result_artifacts.insert().values(
                id=result,
                project_id=project,
                job_id=job,
                attempt=1,
                stage="candidate_extraction",
                manifest_asset_id=manifest_asset,
                manifest_sha256=digest,
            )
        )
        await connection.execute(
            m.glyphs.insert().values(
                id=glyph, project_id=project, codepoints=[point], coverage_status="needs_review"
            )
        )
        await connection.execute(
            m.glyph_candidates.insert().values(
                id=candidate,
                result_artifact_id=result,
                project_id=project,
                glyph_id=glyph,
                source_sample_id=uuid.UUID(sample["sample_id"]),
                origin=origin,
                rank=1,
                outline_inline=geometry,
                metrics=geometry["metrics"],
                content_hash=bytes.fromhex(content_hash(geometry).removeprefix("sha256:")),
                evidence={"recognition_score": None, "outline_validity": "pass"},
            )
        )
    service = GlyphsService(engine, identity, storage)
    return owner, user, project, glyph, candidate, service


async def select(service, owner, project, glyph, candidate):
    current = await service.get(owner, project, glyph)
    return await service.select(
        owner,
        project,
        glyph,
        body=SelectCandidate(
            candidate_id=candidate, base_revision_id=current["working_revision_id"]
        ),
        if_match=f'"v{current["row_version"]}"',
        idempotency_key=uuid.uuid4().hex,
    )


async def action(service, owner, project, glyph, current, kind, **fields):
    return await service.update(
        owner,
        project,
        glyph,
        body=GlyphPatch(base_revision_id=current["working_revision_id"], action=kind, **fields),
        if_match=f'"v{current["row_version"]}"',
    )


def test_candidate_selection_is_pending_approval_explicit_and_retries_never_replace_active(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        before = await service.get(owner, project, glyph)
        assert before["active_revision_id"] is None and before["recommended_candidate_id"] == str(
            candidate
        )
        key = uuid.uuid4().hex
        body = SelectCandidate(candidate_id=candidate, base_revision_id=None)
        selected = await service.select(
            owner, project, glyph, body=body, if_match='"v1"', idempotency_key=key
        )
        assert (
            selected["active_revision_id"] is None
            and selected["revisions"][0]["approval"] == "pending"
        )
        approved = await action(service, owner, project, glyph, selected, "approve")
        assert approved["active_revision_id"] == selected["working_revision_id"]
        assert approved["active_outline"] == outline()
        # A request replay returns its original pending receipt, while durable approval stays intact.
        replay = await service.select(
            owner, project, glyph, body=body, if_match='"v1"', idempotency_key=key
        )
        assert replay["working_revision_id"] == selected["working_revision_id"]
        durable = await service.get(owner, project, glyph)
        assert (
            durable["active_revision_id"] == approved["active_revision_id"]
            and len(durable["revisions"]) == 1
        )
        alternatives = await service.candidates(owner, project, glyph)
        assert alternatives["items"][0]["review_status"] == "approved"

    execute(uploads_database, tmp_path, scenario)


def test_concurrent_edits_etag_base_revision_autosave_suppression_and_rollback_new_revision(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        selected = await select(service, owner, project, glyph, candidate)
        approved = await action(service, owner, project, glyph, selected, "approve")
        same = await action(
            service,
            owner,
            project,
            glyph,
            approved,
            "edit",
            outline=GlyphOutline.model_validate(approved["outline"]),
        )
        assert same["row_version"] == approved["row_version"] and len(same["revisions"]) == 1
        results = await asyncio.gather(
            *(
                action(
                    service,
                    owner,
                    project,
                    glyph,
                    approved,
                    "edit",
                    outline=GlyphOutline.model_validate(outline(width=width)),
                )
                for width in (540.0, 560.0)
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(value, Exception) for value in results) == 1
        failure = next(value for value in results if isinstance(value, Exception))
        assert isinstance(failure, ApiError) and failure.code == "revision_conflict"
        current = await service.get(owner, project, glyph)
        assert current["active_revision_id"] == approved["active_revision_id"]
        with pytest.raises(ApiError) as conflict:
            await service.update(
                owner,
                project,
                glyph,
                body=GlyphPatch(base_revision_id=None, action="approve"),
                if_match=f'"v{current["row_version"]}"',
            )
        assert conflict.value.status == 412
        restored = await action(
            service,
            owner,
            project,
            glyph,
            current,
            "rollback",
            target_revision_id=uuid.UUID(approved["active_revision_id"]),
        )
        assert len(restored["revisions"]) == 3
        assert (
            restored["revisions"][0]["origin"] == "restored"
            and restored["revisions"][0]["approval"] == "pending"
        )
        assert (
            restored["working_revision_id"] != approved["active_revision_id"]
            and restored["outline"] == approved["outline"]
        )

    execute(uploads_database, tmp_path, scenario)


def test_generated_candidate_unapproved_activation_and_cross_owner_references_fail(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads, origin="geometric_reconstruction"
        )
        selected = await select(service, owner, project, glyph, candidate)
        assert selected["revisions"][0]["origin"] == "ml_generated"
        assert selected["active_revision_id"] is None
        assert selected["recommended_candidate_id"] is None
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    m.glyphs.update()
                    .where(m.glyphs.c.id == glyph)
                    .values(active_revision_id=uuid.UUID(selected["working_revision_id"]))
                )
        foreign = principal()
        await identity.me(foreign)
        for operation in (
            lambda: service.get(foreign, project, glyph),
            lambda: service.list(foreign, project),
            lambda: service.candidates(foreign, project, glyph),
            lambda: service.coverage(foreign, project),
            lambda: service.prompts(foreign, project),
            lambda: service.snapshot(
                foreign, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
            ),
        ):
            with pytest.raises(ApiError) as error:
                await operation()
            assert error.value.status == 404
        rejected = await action(service, owner, project, glyph, selected, "reject")
        assert rejected["active_revision_id"] is None and rejected["working_revision_id"] is None
        assert (await service.coverage(owner, project))["generated_pending"] == []

    execute(uploads_database, tmp_path, scenario)


def test_snapshot_freezes_approved_geometry_and_blocks_revision_pruning(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        selected = await select(service, owner, project, glyph, candidate)
        approved = await action(service, owner, project, glyph, selected, "approve")
        key = uuid.uuid4().hex
        frozen = await service.snapshot(
            owner, project, body=SnapshotCreate(label="First approval"), idempotency_key=key
        )
        assert frozen["manifest"]["glyphs"] == {str(glyph): approved["active_revision_id"]}
        assert frozen["manifest_sha256"] == content_hash(frozen["manifest"])
        changed = await action(
            service,
            owner,
            project,
            glyph,
            approved,
            "edit",
            outline=GlyphOutline.model_validate(outline(width=620.0)),
        )
        await action(service, owner, project, glyph, changed, "approve")
        assert (await service.get_snapshot(owner, project, uuid.UUID(frozen["id"])))[
            "manifest"
        ] == frozen["manifest"]
        for statement in (
            m.project_snapshots.update()
            .where(m.project_snapshots.c.id == uuid.UUID(frozen["id"]))
            .values(label="mutated"),
            m.glyph_revisions.delete().where(
                m.glyph_revisions.c.id == uuid.UUID(approved["active_revision_id"])
            ),
        ):
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(statement)
        replay = await service.snapshot(
            owner, project, body=SnapshotCreate(label="First approval"), idempotency_key=key
        )
        assert replay["id"] == frozen["id"]

    execute(uploads_database, tmp_path, scenario)


def test_space_is_metrics_only_and_coverage_counts_52_and_95_truthfully(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal()
        user, project = await create_project(identity, projects, owner)
        service = GlyphsService(engine, identity, storage)
        now = datetime.now(UTC)
        async with engine.begin() as connection:
            # Approved canonical fixture inventory: no recognizer or font compiler involved.
            for point in sorted(ASCII - {32}):
                glyph, revision = uuid.uuid4(), uuid.uuid4()
                geometry = outline(point)
                await connection.execute(
                    m.glyphs.insert().values(id=glyph, project_id=project, codepoints=[point])
                )
                await connection.execute(
                    m.glyph_revisions.insert().values(
                        id=revision,
                        glyph_id=glyph,
                        seq=1,
                        origin="user_edit",
                        approval="approved",
                        outline_inline=geometry,
                        metrics=geometry["metrics"],
                        content_hash=bytes.fromhex(content_hash(geometry)[7:]),
                        created_by_user_id=user,
                        approved_by_user_id=user,
                        approved_at=now,
                    )
                )
                await connection.execute(
                    m.glyphs.update()
                    .where(m.glyphs.c.id == glyph)
                    .values(active_revision_id=revision, coverage_status="approved")
                )
        coverage = await service.coverage(owner, project)
        assert coverage["core_approved"] == len(CORE) == 52 and coverage["ascii_approved"] == 94
        assert (
            coverage["core_complete"]
            and not coverage["ascii_inventory_complete"]
            and coverage["missing"] == [32]
        )
        space = await service.space(
            owner, project, advance_width=320.0, idempotency_key="space-fixture"
        )
        assert space["outline"]["contours"] == [] and space["active_revision_id"] is None
        assert (await service.coverage(owner, project))["ascii_approved"] == 94
        await action(service, owner, project, uuid.UUID(space["id"]), space, "approve")
        complete = await service.coverage(owner, project)
        assert complete["ascii_approved"] == 95 and complete["ascii_inventory_complete"]
        assert not complete["complete_font_validated"] and complete["missing"] == []
        assert 32 not in complete["observed"] and 0 not in complete["user_approved"]

    execute(uploads_database, tmp_path, scenario)


def test_candidate_result_and_revision_lineage_immutable_and_scope_forgery_rejected(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        selected = await select(service, owner, project, glyph, candidate)
        with sync.connect() as connection:
            result = connection.scalar(
                sa.select(m.glyph_candidates.c.result_artifact_id).where(
                    m.glyph_candidates.c.id == candidate
                )
            )
        for statement in (
            m.glyph_candidates.update().where(m.glyph_candidates.c.id == candidate).values(rank=2),
            m.ml_result_artifacts.update()
            .where(m.ml_result_artifacts.c.id == result)
            .values(stage="rewritten"),
            m.glyph_revisions.update()
            .where(m.glyph_revisions.c.id == uuid.UUID(selected["working_revision_id"]))
            .values(source_candidate_id=None),
        ):
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(statement)
        foreign_owner, _, foreign_project, foreign_glyph, foreign_candidate, _ = await seed(
            engine, identity, projects, storage, uploads
        )
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                # Same-glyph FK is required, not merely an existing candidate UUID.
                geometry = outline()
                await connection.execute(
                    m.glyph_revisions.insert().values(
                        id=uuid.uuid4(),
                        glyph_id=glyph,
                        seq=2,
                        origin="user_edit",
                        approval="pending",
                        outline_inline=geometry,
                        metrics=geometry["metrics"],
                        content_hash=bytes.fromhex(content_hash(geometry)[7:]),
                        source_candidate_id=foreign_candidate,
                        created_by_user_id=user,
                    )
                )
        with pytest.raises(ApiError) as no_cross_selection:
            await service.select(
                owner,
                project,
                glyph,
                body=SelectCandidate(
                    candidate_id=foreign_candidate, base_revision_id=selected["working_revision_id"]
                ),
                if_match=f'"v{selected["row_version"]}"',
                idempotency_key=uuid.uuid4().hex,
            )
        assert no_cross_selection.value.status == 404

    execute(uploads_database, tmp_path, scenario)


def test_unsafe_geometry_and_unknown_template_context_never_become_edit_truth(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        current = await select(service, owner, project, glyph, candidate)
        mismatched = outline(66)
        with pytest.raises(ApiError) as wrong_mapping:
            await action(
                service,
                owner,
                project,
                glyph,
                current,
                "edit",
                outline=GlyphOutline.model_validate(mismatched),
            )
        assert wrong_mapping.value.status == 400
        prompts = await service.prompts(owner, project)
        assert (
            prompts["capture_type"] == "isolated_characters"
            and not prompts["prompt_is_ground_truth"]
        )
        assert prompts["natural_cursive_status"] == "experimental_unavailable"
        async with engine.begin() as connection:
            await connection.execute(
                m.projects.update().where(m.projects.c.id == project).values(status="deleting")
            )
        with pytest.raises(ApiError) as deleting:
            await action(service, owner, project, glyph, current, "approve")
        assert deleting.value.status == 409

    execute(uploads_database, tmp_path, scenario)


def test_extraction_migration_empty_cycle_and_seeded_downgrade_refusal(uploads_database, tmp_path):
    sync, schema = uploads_database
    with sync.begin() as connection:
        config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
        config.attributes["connection"] = connection
        command.downgrade(config, "0005_publication_fence")
        command.upgrade(config, "0006_extraction_review")
        # Current service binaries require the current additive schema. The empty
        # historical cycle above and refusal below still exercise 0006 itself.
        command.upgrade(config, "head")

    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        await seed(engine, identity, projects, storage, uploads)

    execute(uploads_database, tmp_path, scenario)
    with pytest.raises(sa.exc.DatabaseError, match="forward_repair"):
        with sync.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0005_publication_fence")


def test_snapshot_copies_profile_values_and_calibration_contract_is_honest(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, glyph, candidate, service = await seed(
            engine, identity, projects, storage, uploads
        )
        profile = uuid.uuid4()
        with sync.begin() as con:
            con.execute(
                m.style_profiles.insert().values(
                    id=profile,
                    project_id=project,
                    version=1,
                    metrics={"baseline": 0.0},
                    reference_glyph_ids=[glyph],
                )
            )
        snapshot = await service.snapshot(
            owner, project, body=SnapshotCreate(), idempotency_key=uuid.uuid4().hex
        )
        assert snapshot["manifest"]["style_profile_snapshot"]["metrics"] == {"baseline": 0.0}
        with sync.begin() as con:
            con.execute(
                m.style_profiles.update()
                .where(m.style_profiles.c.id == profile)
                .values(metrics={"baseline": 50.0})
            )
        assert (await service.get_snapshot(owner, project, uuid.UUID(snapshot["id"])))[
            "manifest"
        ] == snapshot["manifest"]
        prompt = await service.prompts(owner, project)
        assert prompt["definition_version"] == "1.0.0"
        assert prompt["passage_status"] == "unoptimized_example_not_ground_truth"
        assert (
            prompt["prompt_is_ground_truth"] is False
            and prompt["natural_cursive_status"] == "experimental_unavailable"
        )
        assert prompt["capture_type"] == "isolated_characters" and len(prompt["codepoints"]) == 12

    execute(uploads_database, tmp_path, scenario)
