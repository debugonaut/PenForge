"""Synthetic v2 capture through fenced worker, explicit approval and real font bytes."""

import asyncio
import hashlib
import io
import json
import uuid
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.db import models as m
from app.modules.uploads.service import QueueService
from fontTools.ttLib import TTFont
from sqlalchemy.dialects.postgresql import insert
from test_analysis_database import drawing
from test_uploads_database import execute
from test_uploads_database import uploads_database as uploads_database
from test_uploads_http import capture_http as capture_http
from test_uploads_http import headers

from services.worker.analysis import AnalysisWorker
from services.worker.exports import ExportWorker
from services.worker.runtime import PipelineWorker

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_v2_http_capture_approval_and_all_real_font_formats(capture_http, tmp_path):
    client, sync, users, projects, uploads = capture_http
    uploads.upload_max_bytes = 2 * 1024 * 1024
    with sync.begin() as con:
        con.execute(m.quota_balances.update().values(limit_amount=100_000_000))
        for resource in ["exports", "compute_seconds"]:
            for scope in ["user", "global"]:
                con.execute(
                    insert(m.quota_balances)
                    .values(
                        id=uuid.uuid4(),
                        scope=scope,
                        user_id=users[0] if scope == "user" else None,
                        resource=resource,
                        window_kind="lifetime",
                        window_start=date(1970, 1, 1),
                        limit_amount=10000,
                    )
                    .on_conflict_do_nothing()
                )
    base = f"/api/v1/projects/{projects[0]}"
    document = json.loads((ROOT / "packages/contracts/fixtures/strokes.v1.json").read_bytes())
    saved = client.post(
        base + "/samples",
        headers=headers(key=uuid.uuid4().hex),
        json={"source_kind": "strokes", "stroke_document": document},
    )
    assert saved.status_code == 201, saved.text
    sample = saved.json()
    body = {
        "job_type": "analyze_samples",
        "drawing_mode": "fixed_round_v1",
        "sample_ids": [sample["sample_id"]],
        "assignments": [
            {
                "sample_id": sample["sample_id"],
                "codepoints": [103],
                "capture_transform": {"baseline_y": 0.75, "units_per_px": 2, "pen_width_px": 3.3},
            }
        ],
    }
    key = uuid.uuid4().hex
    requested = client.post(base + "/jobs", headers=headers(key=key), json=body)
    assert requested.status_code == 202, requested.text
    assert (
        client.post(base + "/jobs", headers=headers(key=key), json=body).headers[
            "Idempotency-Replayed"
        ]
        == "true"
    )
    service = client.app.state.analysis_service
    # A worker advertising only v1 must leave v2 queued.
    assert asyncio.run(service.claim("old-worker", supported_versions=(1,))) is None
    with sync.connect() as con:
        row = (
            con.execute(
                sa.select(m.jobs).where(m.jobs.c.id == uuid.UUID(requested.json()["job_id"]))
            )
            .mappings()
            .one()
        )
        assert row["payload_schema_version"] == 2 and row["state"] == "queued"
    pipeline = PipelineWorker(
        QueueService(uploads.engine, uploads),
        uploads,
        uploads.storage,
        worker_id="canvas-pipeline",
        temp_root=tmp_path / "verify",
        analysis=AnalysisWorker(service, worker_id="round", temp_root=tmp_path / "analysis"),
        exports=ExportWorker(
            client.app.state.exports_service,
            worker_id="round-export",
            temp_root=tmp_path / "export",
        ),
    )
    assert asyncio.run(pipeline.run_once())
    result = client.get(base + "/analysis-jobs/" + requested.json()["job_id"], headers=headers())
    assert result.json()["state"] == "succeeded", result.text
    glyph = client.get(base + "/glyphs", headers=headers()).json()["items"][0]
    path = base + "/glyphs/" + glyph["id"]
    candidates = client.get(path + "/candidates", headers=headers()).json()["items"]
    assert candidates[0]["evidence"]["provenance"]["algorithm_version"] == "capture-round-stroke-v1"
    assert glyph["active_revision_id"] is None
    assert (
        client.post(
            base + "/exports",
            headers=headers(key=uuid.uuid4().hex),
            json={"formats": ["ttf"], "family_name": "SyntheticRound"},
        ).status_code
        == 409
    )
    selected = client.post(
        path + "/select-candidate",
        headers={**headers(key=uuid.uuid4().hex), "If-Match": '"v1"'},
        json={"candidate_id": candidates[0]["id"], "base_revision_id": None},
    )
    assert selected.status_code == 200 and selected.json()["active_revision_id"] is None
    assert (
        client.post(
            base + "/exports",
            headers=headers(key=uuid.uuid4().hex),
            json={"formats": ["ttf"], "family_name": "SyntheticRound"},
        ).status_code
        == 409
    )
    approved = client.patch(
        path,
        headers={**headers(), "If-Match": selected.headers["ETag"]},
        json={"action": "approve", "base_revision_id": selected.json()["working_revision_id"]},
    )
    assert approved.status_code == 200, approved.text
    export = client.post(
        base + "/exports",
        headers=headers(key=uuid.uuid4().hex),
        json={"formats": ["ttf", "otf", "woff2"], "family_name": "SyntheticRound"},
    )
    assert export.status_code == 202, export.text
    assert asyncio.run(pipeline.run_once())
    for eid in export.json()["export_ids"]:
        metadata = client.get(f"/api/v1/exports/{eid}", headers=headers()).json()
        assert metadata["status"] == "ready", metadata
        download = client.get(f"/api/v1/exports/{eid}/download-url", headers=headers()).json()
        assert client.get(download["url"], headers=headers("other-token")).status_code == 404
        font = client.get(download["url"], headers=headers())
        assert font.status_code == 200
        assert metadata["sha256"] == "sha256:" + hashlib.sha256(font.content).hexdigest()
        parsed = TTFont(io.BytesIO(font.content))
        assert parsed.getBestCmap()[103] in parsed.getGlyphOrder()
        assert ("CFF " in parsed) == (metadata["format"] == "otf")
    stored = client.get(base + "/samples", headers=headers()).json()["items"][0]
    assert stored["capture_size_px"] == document["capture_size_px"]


def test_v2_forged_brush_provenance_cannot_publish(uploads_database, tmp_path):
    async def scenario(sync, engine, identity, projects, storage, uploads, queue):
        from app.core.errors import ApiError
        from app.modules.analysis.service import AnalysisArtifact, AnalysisService, json_bytes
        from penforge_contracts import content_hash

        owner, _, project, _, body = await drawing(engine, identity, projects, uploads)
        body = body.model_copy(update={"drawing_mode": "fixed_round_v1"})
        service = AnalysisService(engine, identity, uploads, storage)
        await service.create(owner, project, uuid.uuid4().hex, body)
        job = await service.claim("forgery")
        artifacts = []
        stage = service.stage

        async def collect(job, holder, produced):
            artifacts.extend(produced)
            return False

        service.stage = collect
        assert not await AnalysisWorker(
            service, worker_id="forgery", temp_root=tmp_path / "attempt"
        ).execute(job)
        service.stage = stage
        manifest = json.loads(artifacts[-1].data)
        report_item = artifacts[-2]
        report = json.loads(report_item.data)
        next(iter(report["candidates"].values()))["provenance"]["geometry_policy_hash"] = (
            "sha256:" + "f" * 64
        )
        artifacts[-2] = AnalysisArtifact(
            report_item.artifact_id, report_item.kind, report_item.media_type, json_bytes(report)
        )
        manifest["quality_report"]["sha256"] = artifacts[-2].digest
        manifest["quality_report"]["size_bytes"] = len(artifacts[-2].data)
        manifest["artifact_hash"] = content_hash(manifest, exclude_root_keys={"artifact_hash"})
        item = artifacts[-1]
        artifacts[-1] = AnalysisArtifact(
            item.artifact_id, item.kind, item.media_type, json_bytes(manifest)
        )
        assert await service.stage(job, "forgery", artifacts)
        with pytest.raises(ApiError) as error:
            await service.publish(job, "forgery", item.artifact_id)
        assert error.value.code == "upload_mismatch"
        with sync.connect() as con:
            assert con.scalar(sa.select(sa.func.count()).select_from(m.glyph_candidates)) == 0
        assert await service.fail(job, "forgery", "invalid_input")

    execute(uploads_database, tmp_path, scenario)
