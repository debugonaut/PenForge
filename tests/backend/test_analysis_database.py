"""Real durable Track C/A publication, fencing and review integration."""

import hashlib
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.core.errors import ApiError
from app.db import models as m
from app.modules.analysis.schemas import AnalysisCreate
from app.modules.analysis.service import AnalysisService
from app.modules.glyphs.schemas import GlyphPatch, SelectCandidate, SnapshotCreate
from app.modules.glyphs.service import GlyphsService
from app.modules.uploads.schemas import StrokeSampleCreate
from penforge_contracts import StrokeDocument
from test_uploads_database import configure, create_project, execute, principal
from test_uploads_database import uploads_database as uploads_database

from services.worker.analysis import AnalysisWorker

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


async def drawing(engine, identity, projects, uploads, owner=None, *, pen=True):
    owner = owner or principal()
    user, project = await create_project(identity, projects, owner)
    await configure(engine, user)
    doc = StrokeDocument.model_validate_json(
        (ROOT / "packages/contracts/fixtures/strokes.v1.json").read_bytes()
    )
    if not pen:
        value = doc.model_dump(mode="json")
        for stroke in value["strokes"]:
            stroke["pointer_type"] = "mouse"
            for point in stroke["points"]:
                for key in ("pressure", "tilt_x", "tilt_y", "twist"):
                    point[key] = None
        doc = StrokeDocument.model_validate(value)
    _, sample = await uploads.register_sample(
        owner,
        project,
        idempotency_key=uuid.uuid4().hex,
        body=StrokeSampleCreate(source_kind="strokes", stroke_document=doc),
    )
    body = AnalysisCreate(
        sample_ids=[sample["sample_id"]],
        assignments=[
            dict(
                sample_id=sample["sample_id"],
                codepoints=[103],
                capture_transform=dict(baseline_y=0.75, units_per_px=1.0, pen_width_px=3.0),
            )
        ],
    )
    return owner, user, project, sample, body


@pytest.mark.parametrize("pen", [True, False])
def test_digital_capture_actual_isolated_worker_review_snapshot(uploads_database, tmp_path, pen):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, sample, body = await drawing(
            engine, identity, projects, uploads, pen=pen
        )
        service = AnalysisService(engine, identity, uploads, storage)
        key = uuid.uuid4().hex
        replay, created = await service.create(owner, project, key, body)
        assert not replay
        assert (await service.create(owner, project, key, body))[0]
        with pytest.raises(ApiError) as foreign:
            await service.get(principal(), project, uuid.UUID(created["job_id"]))
        assert foreign.value.status == 404
        worker = AnalysisWorker(
            service, worker_id="digital", temp_root=tmp_path / "attempts", heartbeat_seconds=0.1
        )
        job = await service.claim("digital")
        assert job
        assert await worker.execute(job)
        result = await service.get(owner, project, job["id"])
        assert result["state"] == "succeeded" and result["requires_user_action"]
        glyphs = GlyphsService(engine, identity, storage)
        glyph = (await glyphs.list(owner, project))["items"][0]
        assert glyph["active_revision_id"] is None and glyph["working_revision_id"] is None
        candidates = (await glyphs.candidates(owner, project, uuid.UUID(glyph["id"])))["items"]
        assert len(candidates) == 1 and candidates[0]["origin"] == "manual_drawing"
        provenance = candidates[0]["evidence"]["provenance"]
        assert (
            provenance["image_segmentation"] is False
            and provenance["ocr"] is False
            and provenance["source_unchanged"]
        )
        key = uuid.uuid4().hex
        selected = await glyphs.select(
            owner,
            project,
            uuid.UUID(glyph["id"]),
            body=SelectCandidate(candidate_id=candidates[0]["id"], base_revision_id=None),
            if_match='"v1"',
            idempotency_key=uuid.uuid4().hex,
        )
        assert selected["active_revision_id"] is None
        approved = await glyphs.update(
            owner,
            project,
            uuid.UUID(glyph["id"]),
            body=GlyphPatch(action="approve", base_revision_id=selected["working_revision_id"]),
            if_match=f'"v{selected["row_version"]}"',
        )
        snap = await glyphs.snapshot(
            owner, project, body=SnapshotCreate(label="Track C"), idempotency_key=uuid.uuid4().hex
        )
        assert approved["active_revision_id"] in json.dumps(snap["manifest"])
        with sync.connect() as con:
            raw = (
                con.execute(
                    sa.select(m.assets).where(m.assets.c.id == uuid.UUID(sample["asset_id"]))
                )
                .mappings()
                .one()
            )
            ledger = con.execute(sa.select(m.quota_balances)).mappings().all()
        assert (
            hashlib.sha256(storage.read_object(raw["storage_key"], max_bytes=1_000_000)).digest()
            == raw["sha256"]
        )
        assert all(row["reserved"] == 0 for row in ledger)
        # A later run adds alternatives, preserving the approved selection/snapshot.
        _, again = await service.create(owner, project, uuid.uuid4().hex, body)
        assert await worker.run_once()
        after = await glyphs.get(owner, project, uuid.UUID(glyph["id"]))
        assert after["active_revision_id"] == approved["active_revision_id"]
        assert (await glyphs.get_snapshot(owner, project, uuid.UUID(snap["id"]))) == snap

    execute(uploads_database, tmp_path, scenario)


async def prepared(engine, identity, projects, storage, uploads, tmp_path):
    owner, user, project, sample, body = await drawing(engine, identity, projects, uploads)
    service = AnalysisService(engine, identity, uploads, storage)
    await service.create(owner, project, uuid.uuid4().hex, body)
    job = await service.claim("prepare")
    saved = []
    stage = service.stage

    async def collect(job, holder, artifacts):
        saved.extend(artifacts)
        return False

    service.stage = collect
    worker = AnalysisWorker(service, worker_id="prepare", temp_root=tmp_path / "attempts")
    assert not await worker.execute(job)
    service.stage = stage
    return owner, user, project, service, job, saved


@pytest.mark.parametrize(
    "forgery", ["attempt", "source", "model", "origin", "mapping", "child_hash", "policy"]
)
def test_forged_result_rejected_without_registration(uploads_database, tmp_path, forgery):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        from app.modules.analysis.service import AnalysisArtifact, json_bytes
        from penforge_contracts import content_hash

        owner, user, project, service, job, artifacts = await prepared(
            engine, identity, projects, storage, uploads, tmp_path
        )
        manifest = json.loads(artifacts[-1].data)
        if forgery == "attempt":
            manifest["fencing_token"] += 1
        elif forgery == "source":
            manifest["input"]["source_hashes"][0] = "sha256:" + "f" * 64
        elif forgery == "model":
            manifest["pipeline"].update(
                model_id="unvalidated", model_version="1.0.0", model_hash="sha256:" + "f" * 64
            )
        elif forgery == "origin":
            manifest["candidates"][0]["origin"] = "style_conditioned_generation"
        elif forgery == "mapping":
            manifest["candidates"][0]["codepoints"] = [65]
        elif forgery == "child_hash":
            manifest["candidates"][0]["outline"]["sha256"] = "sha256:" + "f" * 64
        elif forgery == "policy":
            item = artifacts[-2]
            report = json.loads(item.data)
            report["policy_hash"] = "sha256:" + "f" * 64
            artifacts[-2] = AnalysisArtifact(
                item.artifact_id, item.kind, item.media_type, json_bytes(report)
            )
            manifest["quality_report"]["sha256"] = artifacts[-2].digest
            manifest["quality_report"]["size_bytes"] = len(artifacts[-2].data)
        manifest["artifact_hash"] = content_hash(manifest, exclude_root_keys={"artifact_hash"})
        item = artifacts[-1]
        artifacts[-1] = AnalysisArtifact(
            item.artifact_id, item.kind, item.media_type, json_bytes(manifest)
        )
        assert await service.stage(job, "prepare", artifacts)
        with pytest.raises(ApiError) as error:
            await service.publish(job, "prepare", artifacts[-1].artifact_id)
        assert error.value.code == "upload_mismatch"
        with sync.connect() as con:
            assert con.scalar(sa.select(sa.func.count()).select_from(m.ml_result_artifacts)) == 0
            assert con.scalar(sa.select(sa.func.count()).select_from(m.glyph_candidates)) == 0
        assert await service.fail(job, "prepare", "invalid_input")

    execute(uploads_database, tmp_path, scenario)


def test_crashed_staging_lease_reaped_and_stale_attempt_cannot_publish(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner, user, project, service, job, artifacts = await prepared(
            engine, identity, projects, storage, uploads, tmp_path
        )
        assert await service.stage(job, "prepare", artifacts)
        keys = [service.key(job, a.artifact_id) for a in artifacts]
        with sync.begin() as con:
            con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(lease_expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'"))
            )
        assert await service.reap_expired() == 1
        assert not await service.publish(job, "prepare", artifacts[-1].artifact_id)
        assert not await service.stage(job, "prepare", artifacts)
        with sync.connect() as con:
            rows = (
                con.execute(
                    sa.select(m.assets).where(m.assets.c.id.in_([a.artifact_id for a in artifacts]))
                )
                .mappings()
                .all()
            )
        assert all(r["cleanup_completed_at"] for r in rows)
        for key in keys:
            from app.adapters.storage.base import StorageError

            with pytest.raises(StorageError):
                storage.read_object(key, max_bytes=1_000_000)
        assert await service.claim("new")

    execute(uploads_database, tmp_path, scenario)


def test_analysis_commit_time_lease_fence(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        import time

        owner, user, project, service, job, artifacts = await prepared(
            engine, identity, projects, storage, uploads, tmp_path
        )
        assert await service.stage(job, "prepare", artifacts)
        with sync.begin() as con:
            con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(
                    lease_expires_at=sa.func.clock_timestamp() + sa.text("interval '0.3 second'")
                )
            )
        verify = storage.verify_upload

        def slow(*args, **kwargs):
            time.sleep(0.2)
            return verify(*args, **kwargs)

        storage.verify_upload = slow
        with pytest.raises(ApiError) as error:
            await service.publish(job, "prepare", artifacts[-1].artifact_id)
        assert error.value.code == "state_conflict"
        with sync.connect() as con:
            assert con.scalar(sa.select(sa.func.count()).select_from(m.ml_result_artifacts)) == 0
            assert (
                con.scalar(sa.select(m.jobs.c.state).where(m.jobs.c.id == job["id"])) == "running"
            )
        assert await service.reap_expired() == 1

    execute(uploads_database, tmp_path, scenario)


def test_template_image_actual_verification_extraction_and_immutable_evidence(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        from app.modules.uploads.schemas import AssetSampleCreate, TemplateReference, UploadInit

        from packages.extraction.fixtures import synthetic_sheet
        from packages.extraction.templates import load_template, template_hash
        from services.worker.runtime import VerificationWorker

        owner = principal()
        user, project = await create_project(identity, projects, owner)
        await configure(engine, user, amount=100_000_000)
        manifest = load_template()
        digest = template_hash(manifest)
        uploads.templates[
            (manifest.template_id, manifest.template_version, manifest.page.index)
        ] = (manifest, digest)
        uploads.derivative_max_bytes = 4 * 1024 * 1024
        data, ground = synthetic_sheet(manifest)
        reference = TemplateReference(
            template_id=manifest.template_id,
            template_version=manifest.template_version,
            manifest_sha256=digest,
            page_index=manifest.page.index,
        )
        _, initialized = await uploads.initialize(
            owner,
            project,
            idempotency_key=uuid.uuid4().hex,
            body=UploadInit(
                filename="licensed-synthetic.png",
                declared_mime="image/png",
                declared_size=len(data),
                source_type="template_scan",
                sha256="sha256:" + hashlib.sha256(data).hexdigest(),
                template=reference,
            ),
        )
        await uploads.put_capability(initialized["upload"]["url"].rsplit("/", 1)[1], data)
        aid = uuid.UUID(initialized["asset_id"])
        await uploads.complete(owner, aid)
        assert await VerificationWorker(
            queue, uploads, storage, worker_id="verify-template", temp_root=tmp_path / "verify"
        ).run_once()
        assert (await uploads.get(owner, aid))["state"] == "ready"
        _, sample = await uploads.register_sample(
            owner,
            project,
            idempotency_key=uuid.uuid4().hex,
            body=AssetSampleCreate(
                source_kind="asset", source_type="template_scan", asset_id=aid, template=reference
            ),
        )
        service = AnalysisService(engine, identity, uploads, storage)
        _, created = await service.create(
            owner, project, uuid.uuid4().hex, AnalysisCreate(sample_ids=[sample["sample_id"]])
        )
        worker = AnalysisWorker(
            service, worker_id="image", temp_root=tmp_path / "analysis", heartbeat_seconds=0.1
        )
        assert await worker.run_once()
        job = await service.get(owner, project, uuid.UUID(created["job_id"]))
        assert job["state"] == "succeeded"
        with sync.connect() as con:
            candidates = con.execute(sa.select(m.glyph_candidates)).mappings().all()
            glyphs = con.execute(sa.select(m.glyphs)).mappings().all()
            results = con.execute(sa.select(m.ml_result_artifacts)).mappings().all()
            original = con.execute(sa.select(m.assets).where(m.assets.c.id == aid)).mappings().one()
        assert len(candidates) == len(manifest.cells) == 49
        assert len(results) == 1 and all(g["active_revision_id"] is None for g in glyphs)
        assert all(
            c["origin"] == "authentic_extraction"
            and "identity_unverified" in c["evidence"]["flags"]
            and c["evidence"]["recognition"] is None
            and c["evidence"]["crop_artifact"]
            and c["evidence"]["mask_artifact"]
            for c in candidates
        )
        assert storage.read_object(original["storage_key"], max_bytes=24_000_000) == data

    execute(uploads_database, tmp_path, scenario)


@pytest.mark.parametrize("event", ["claim", "delete"])
def test_claim_or_deletion_revokes_old_attempt_and_retains_inventoried_cleanup(
    uploads_database, tmp_path, event
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        owner = principal(anonymous=event == "claim")
        owner, user, project, sample, body = await drawing(
            engine, identity, projects, uploads, owner
        )
        service = AnalysisService(engine, identity, uploads, storage)
        await service.create(owner, project, uuid.uuid4().hex, body)
        job = await service.claim("old-holder")
        if event == "claim":
            destination = principal()
            await identity.claim(destination, owner)
        else:
            # Phase 07 erasure remains gated; exercise its committed deleting state.
            with sync.begin() as con:
                con.execute(
                    m.projects.update().where(m.projects.c.id == project).values(status="deleting")
                )
        assert not await service.heartbeat(job, "old-holder", 0)
        assert not await service.publish(job, "old-holder", uuid.uuid4())
        with sync.begin() as con:
            con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(lease_expires_at=sa.func.clock_timestamp() - sa.text("interval '1 second'"))
            )
        await service.reap_expired()
        with sync.connect() as con:
            assert con.scalar(sa.select(m.jobs.c.state).where(m.jobs.c.id == job["id"])) in {
                "queued",
                "canceled",
            }

    execute(uploads_database, tmp_path, scenario)


def test_separate_worker_role_runs_but_cannot_write_revisions_or_activate(
    uploads_database, tmp_path
):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        from sqlalchemy.exc import DBAPIError
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        from infrastructure.migrations.grant_worker_role import grant_worker_role

        owner, user, project, sample, body = await drawing(engine, identity, projects, uploads)
        api = AnalysisService(engine, identity, uploads, storage)
        await api.create(owner, project, uuid.uuid4().hex, body)
        role = "worker_test_" + uuid.uuid4().hex
        schema = uploads_database[1]
        with sync.begin() as con:
            con.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOINHERIT')
            grant_worker_role(con, role, schema)
        worker_engine = create_async_engine(
            sync.url,
            connect_args={"options": f"-csearch_path={schema} -crole={role}"},
            poolclass=NullPool,
        )
        try:
            from app.modules.uploads.service import UploadsService

            worker_uploads = UploadsService(worker_engine, identity, storage)
            service = AnalysisService(worker_engine, identity, worker_uploads, storage)
            worker = AnalysisWorker(
                service, worker_id="restricted-worker", temp_root=tmp_path / "attempts"
            )
            assert await worker.run_once()
            with sync.connect() as con:
                assert con.scalar(sa.select(sa.func.count()).select_from(m.glyph_candidates)) == 1
            for statement in (
                m.glyph_revisions.insert().values(id=uuid.uuid4()),
                m.glyphs.update().values(active_revision_id=None),
                m.glyph_candidates.update().values(review_status="approved"),
                m.project_snapshots.insert().values(id=uuid.uuid4()),
            ):
                with pytest.raises(DBAPIError):
                    async with worker_engine.begin() as con:
                        await con.execute(statement)
        finally:
            await worker_engine.dispose()
            with sync.begin() as con:
                con.exec_driver_sql(f'DROP OWNED BY "{role}"')
                con.exec_driver_sql(f'DROP ROLE "{role}"')

    execute(uploads_database, tmp_path, scenario)


def test_detailed_outline_uses_immutable_asset_and_disables_inline_edit(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        import math

        from app.modules.analysis.service import AnalysisArtifact, json_bytes
        from penforge_contracts import GlyphOutline, content_hash

        owner, user, project, service, job, artifacts = await prepared(
            engine, identity, projects, storage, uploads, tmp_path
        )
        shape = json.loads(artifacts[0].data)
        points = [
            dict(
                x=round(250 + 200 * math.cos(i * 2 * math.pi / 650), 2),
                y=round(200 + 200 * math.sin(i * 2 * math.pi / 650), 2),
            )
            for i in range(650)
        ]
        shape["contours"] = [
            dict(
                closed=True,
                start=points[0],
                segments=[dict(type="line", to=p) for p in [*points[1:], points[0]]],
            )
        ]
        outline = GlyphOutline.model_validate(shape)
        blob = outline.model_dump_json().encode()
        assert len(blob) <= 32000 < len(json.dumps(outline.model_dump(mode="json")).encode())
        original = artifacts[0]
        artifacts[0] = AnalysisArtifact(
            original.artifact_id, original.kind, original.media_type, blob
        )
        manifest = json.loads(artifacts[-1].data)
        quality = json.loads(artifacts[-2].data)
        cid = manifest["candidates"][0]["candidate_id"]
        quality["candidates"][cid]["provenance"]["outline_hash"] = content_hash(
            outline.model_dump(mode="json")
        )
        report = artifacts[-2]
        artifacts[-2] = AnalysisArtifact(
            report.artifact_id, report.kind, report.media_type, json_bytes(quality)
        )
        manifest["quality_report"].update(
            sha256=artifacts[-2].digest, size_bytes=len(artifacts[-2].data)
        )
        manifest["candidates"][0]["outline"].update(
            sha256=artifacts[0].digest, size_bytes=len(blob)
        )
        manifest["artifact_hash"] = content_hash(manifest, exclude_root_keys={"artifact_hash"})
        original = artifacts[-1]
        artifacts[-1] = AnalysisArtifact(
            original.artifact_id, original.kind, original.media_type, json_bytes(manifest)
        )
        assert await service.stage(job, "prepare", artifacts)
        assert await service.publish(job, "prepare", artifacts[-1].artifact_id)
        glyphs = GlyphsService(engine, identity, storage)
        glyph = (await glyphs.list(owner, project))["items"][0]
        candidate = (await glyphs.candidates(owner, project, uuid.UUID(glyph["id"])))["items"][0]
        key = uuid.uuid4().hex
        selected = await glyphs.select(
            owner,
            project,
            uuid.UUID(glyph["id"]),
            body=SelectCandidate(candidate_id=candidate["id"], base_revision_id=None),
            if_match='"v1"',
            idempotency_key=key,
        )
        assert selected["outline"] == outline.model_dump(mode="json")
        assert selected["outline_edit_supported"] is False
        approved = await glyphs.update(
            owner,
            project,
            uuid.UUID(glyph["id"]),
            body=GlyphPatch(action="approve", base_revision_id=selected["working_revision_id"]),
            if_match=f'"v{selected["row_version"]}"',
        )
        assert approved["active_outline"] == selected["outline"]
        replay = await glyphs.select(
            owner,
            project,
            uuid.UUID(glyph["id"]),
            body=SelectCandidate(candidate_id=candidate["id"], base_revision_id=None),
            if_match='"v1"',
            idempotency_key=key,
        )
        assert replay["outline"] == selected["outline"] and replay["active_revision_id"] is None
        assert replay["revisions"][0]["approval"] == "pending"
        with sync.connect() as con:
            row = con.execute(sa.select(m.glyph_candidates)).mappings().one()
        assert row["outline_inline"] is None and row["outline_asset_id"] is not None

    execute(uploads_database, tmp_path, scenario)
