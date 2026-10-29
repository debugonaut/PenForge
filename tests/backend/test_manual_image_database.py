"""Source-bound ordinary-photo regions through review and real private font bytes."""

import asyncio
import copy
import hashlib
import io
import json
import time
import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from app.db import models as m
from app.modules.uploads.service import QueueService
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw
from sqlalchemy.dialects.postgresql import insert
from test_uploads_database import uploads_database as uploads_database
from test_uploads_http import capture_http as capture_http
from test_uploads_http import headers, initialize

from services.worker.analysis import AnalysisWorker
from services.worker.exports import ExportWorker
from services.worker.runtime import PipelineWorker, VerificationWorker

pytestmark = pytest.mark.integration


def _ordinary_photo():
    """Three known synthetic forms, including two alternatives for the same letter."""
    image = Image.new("RGB", (240, 100), "white")
    draw = ImageDraw.Draw(image)
    for offset in (0, 80):
        draw.line([(15 + offset, 75), (40 + offset, 20), (65 + offset, 75)], fill="black", width=5)
        draw.line([(27 + offset, 52), (53 + offset, 52)], fill="black", width=5)
    draw.line(
        [
            (175, 75),
            (175, 20),
            (207, 20),
            (224, 30),
            (224, 40),
            (209, 48),
            (224, 56),
            (224, 66),
            (207, 75),
            (175, 75),
        ],
        fill="black",
        width=5,
    )
    draw.line([(175, 48), (209, 48)], fill="black", width=5)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image.close()
    return buffer.getvalue()


def _verified_photo(capture_http, tmp_path):
    client, sync, users, projects, uploads = capture_http
    uploads.derivative_max_bytes = 4 * 1024 * 1024
    with sync.begin() as con:
        con.execute(m.quota_balances.update().values(limit_amount=100_000_000))
        for resource in ("exports", "compute_seconds"):
            for scope in ("user", "global"):
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
    data = _ordinary_photo()
    assert len(data) <= 1024  # Existing HTTP fixture deliberately bounds ingress.
    initialized, _, _ = initialize(client, projects[0], data=data)
    assert (
        client.put(initialized["upload"]["url"], content=data, headers=headers()).status_code == 204
    )
    completed = client.post(
        f"/api/v1/uploads/{initialized['asset_id']}/complete", headers=headers()
    )
    assert completed.status_code == 202, completed.text
    worker = VerificationWorker(
        QueueService(uploads.engine, uploads),
        uploads,
        uploads.storage,
        worker_id="manual-photo-verify",
        temp_root=tmp_path / "verification",
    )
    assert asyncio.run(worker.run_once())
    asset = client.get(f"/api/v1/assets/{initialized['asset_id']}", headers=headers())
    assert asset.status_code == 200 and asset.json()["state"] == "ready", asset.text
    base = f"/api/v1/projects/{projects[0]}"
    saved = client.post(
        base + "/samples",
        headers=headers(key=uuid.uuid4().hex),
        json={
            "source_kind": "asset",
            "source_type": "freeform_photo",
            "asset_id": initialized["asset_id"],
        },
    )
    assert saved.status_code == 201, saved.text
    sample = saved.json()
    regions = [
        {
            "region_id": str(uuid.uuid4()),
            "sample_id": sample["sample_id"],
            "source_sha256": sample["source_sha256"],
            "source_width": 240,
            "source_height": 100,
            "rect": {"x": index * 80, "y": 0, "width": 80, "height": 100},
            "codepoints": [codepoint],
            "baseline_y_px": 80.0,
            "units_per_px": 10.0,
            "ink_threshold": 180,
        }
        for index, codepoint in enumerate((65, 65, 66))
    ]
    return (
        data,
        initialized["asset_id"],
        base,
        {
            "job_type": "analyze_samples",
            "image_mode": "manual_regions_v1",
            "sample_ids": [sample["sample_id"]],
            "image_regions": regions,
        },
    )


def test_manual_photo_regions_require_review_then_compile_all_formats(
    capture_http, tmp_path, record_property
):
    client, sync, _, _, uploads = capture_http
    data, asset_id, base, body = _verified_photo(capture_http, tmp_path)
    key = uuid.uuid4().hex
    requested = client.post(base + "/jobs", headers=headers(key=key), json=body)
    assert requested.status_code == 202, requested.text
    replay = client.post(base + "/jobs", headers=headers(key=key), json=body)
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert (
        client.get(
            base + "/analysis-jobs/" + requested.json()["job_id"], headers=headers("other-token")
        ).status_code
        == 404
    )
    service = client.app.state.analysis_service
    assert asyncio.run(service.claim("legacy-photo-worker", supported_versions=(1, 2))) is None
    with sync.connect() as con:
        row = (
            con.execute(
                sa.select(m.jobs).where(m.jobs.c.id == uuid.UUID(requested.json()["job_id"]))
            )
            .mappings()
            .one()
        )
        assert row["payload_schema_version"] == 3 and row["state"] == "queued"
    worker = AnalysisWorker(service, worker_id="manual-photo", temp_root=tmp_path / "analysis")
    pipeline = PipelineWorker(
        QueueService(uploads.engine, uploads),
        uploads,
        uploads.storage,
        worker_id="manual-photo-pipeline",
        temp_root=tmp_path / "pipeline",
        analysis=worker,
        exports=ExportWorker(
            client.app.state.exports_service,
            worker_id="manual-photo-export",
            temp_root=tmp_path / "exports",
        ),
    )
    started = time.perf_counter()
    assert asyncio.run(pipeline.run_once())
    record_property("synthetic_image_analysis_seconds", round(time.perf_counter() - started, 6))
    result = client.get(base + "/analysis-jobs/" + requested.json()["job_id"], headers=headers())
    assert result.json()["state"] == "succeeded", result.text
    assert result.json()["requires_user_action"] is True
    glyphs = client.get(base + "/glyphs", headers=headers()).json()["items"]
    assert {tuple(glyph["codepoints"]) for glyph in glyphs} == {(65,), (66,)}
    assert (
        client.post(
            base + "/exports",
            headers=headers(key=uuid.uuid4().hex),
            json={"formats": ["ttf"], "family_name": "SyntheticManualPhoto"},
        ).status_code
        == 409
    )
    for glyph in glyphs:
        assert glyph["active_revision_id"] is None and glyph["working_revision_id"] is None
        path = base + "/glyphs/" + glyph["id"]
        candidates = client.get(path + "/candidates", headers=headers()).json()["items"]
        assert len(candidates) == (2 if glyph["codepoints"] == [65] else 1)
        for candidate in candidates:
            evidence = candidate["evidence"]
            assert evidence["source_hash"] == "sha256:" + hashlib.sha256(data).hexdigest()
            assert evidence["provenance"]["ocr"] is False
            assert evidence["provenance"]["identity_source"] == "user_assignment"
            assert evidence["provenance"]["source_unchanged"] is True
            assert evidence["provenance"]["assignment"] in body["image_regions"]
        selected = client.post(
            path + "/select-candidate",
            headers={**headers(key=uuid.uuid4().hex), "If-Match": f'"v{glyph["row_version"]}"'},
            json={"candidate_id": candidates[0]["id"], "base_revision_id": None},
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["active_revision_id"] is None
        approved = client.patch(
            path,
            headers={**headers(), "If-Match": selected.headers["ETag"]},
            json={"action": "approve", "base_revision_id": selected.json()["working_revision_id"]},
        )
        assert approved.status_code == 200, approved.text
    export = client.post(
        base + "/exports",
        headers=headers(key=uuid.uuid4().hex),
        json={"formats": ["ttf", "otf", "woff2"], "family_name": "SyntheticManualPhoto"},
    )
    assert export.status_code == 202, export.text
    started = time.perf_counter()
    assert asyncio.run(pipeline.run_once())
    record_property(
        "synthetic_three_format_compile_seconds", round(time.perf_counter() - started, 6)
    )
    formats = set()
    for export_id in export.json()["export_ids"]:
        metadata = client.get(f"/api/v1/exports/{export_id}", headers=headers()).json()
        assert metadata["status"] == "ready", metadata
        assert metadata["coverage_report"]["core_letters_mapped"] == 2
        formats.add(metadata["format"])
        download = client.get(f"/api/v1/exports/{export_id}/download-url", headers=headers()).json()
        assert client.get(download["url"], headers=headers("other-token")).status_code == 404
        font = client.get(download["url"], headers=headers())
        assert font.status_code == 200
        assert metadata["sha256"] == "sha256:" + hashlib.sha256(font.content).hexdigest()
        with TTFont(io.BytesIO(font.content)) as parsed:
            assert {65, 66} <= parsed.getBestCmap().keys()
            assert 67 not in parsed.getBestCmap()
            assert ("CFF " in parsed) == (metadata["format"] == "otf")
    assert formats == {"ttf", "otf", "woff2"}
    with sync.connect() as con:
        original = (
            con.execute(sa.select(m.assets).where(m.assets.c.id == uuid.UUID(asset_id)))
            .mappings()
            .one()
        )
        candidates = con.execute(sa.select(m.glyph_candidates)).mappings().all()
        balances = con.execute(sa.select(m.quota_balances)).mappings().all()
    assert uploads.storage.read_object(original["storage_key"], max_bytes=1_000_000) == data
    assert len(candidates) == 3
    assert all(balance["reserved"] == 0 for balance in balances)


@pytest.mark.parametrize("forgery", ["source_hash", "dimensions", "foreign_owner"])
def test_manual_photo_refuses_changed_source_binding_and_foreign_owner(
    capture_http, tmp_path, forgery
):
    client, sync, _, _, _ = capture_http
    _, _, base, original = _verified_photo(capture_http, tmp_path)
    body = copy.deepcopy(original)
    token = "owner-token"
    if forgery == "source_hash":
        for region in body["image_regions"]:
            region["source_sha256"] = "sha256:" + "0" * 64
    elif forgery == "dimensions":
        for region in body["image_regions"]:
            region["source_width"] = 241
    else:
        token = "other-token"
    response = client.post(base + "/jobs", headers=headers(token, uuid.uuid4().hex), json=body)
    assert response.status_code == (404 if forgery == "foreign_owner" else 400), response.text
    with sync.connect() as con:
        assert (
            con.scalar(
                sa.select(sa.func.count())
                .select_from(m.jobs)
                .where(m.jobs.c.job_type == "analyze_samples")
            )
            == 0
        )
        assert con.scalar(sa.select(sa.func.count()).select_from(m.glyph_candidates)) == 0


@pytest.mark.parametrize("valid_regions", [0, 1])
def test_manual_photo_partial_or_zero_crops_keep_exact_job_quality(
    capture_http, tmp_path, valid_regions
):
    client, sync, _, _, _ = capture_http
    _, _, base, original = _verified_photo(capture_http, tmp_path)
    body = copy.deepcopy(original)
    for region in body["image_regions"][valid_regions:]:
        region["rect"]["height"] = 10  # The source's top margin contains no ink.
    requested = client.post(base + "/jobs", headers=headers(key=uuid.uuid4().hex), json=body)
    assert requested.status_code == 202, requested.text
    worker = AnalysisWorker(
        client.app.state.analysis_service,
        worker_id="partial-photo",
        temp_root=tmp_path / "partial-analysis",
    )
    assert asyncio.run(worker.run_once())
    path = base + "/analysis-jobs/" + requested.json()["job_id"]
    result = client.get(path, headers=headers())
    assert result.status_code == 200 and result.json()["state"] == "succeeded", result.text
    quality = result.json()["quality"]
    assert len(quality) == 1 and quality[0]["sample_id"] == body["sample_ids"][0]
    assert quality[0]["state"] == "needs_better_input"
    assert quality[0]["reasons"] == ["empty_ink"]
    from penforge_contracts import content_hash

    reports = quality[0]["regions"]
    assert len(reports) == 3
    for index, (report, region) in enumerate(zip(reports, body["image_regions"], strict=True)):
        assert report["region_id"] == region["region_id"]
        assert report["assignment"] == region
        assert report["assignment_hash"] == content_hash(region)
        assert report["status"] == ("analyzed" if index < valid_regions else "needs_better_input")
        assert report["reasons"] == ([] if index < valid_regions else ["empty_ink"])
    glyphs = client.get(base + "/glyphs", headers=headers()).json()["items"]
    assert [glyph["codepoints"] for glyph in glyphs] == ([[65]] if valid_regions else [])
    assert all(glyph["active_revision_id"] is None for glyph in glyphs)
    with sync.connect() as con:
        assert (
            con.scalar(sa.select(sa.func.count()).select_from(m.glyph_candidates)) == valid_regions
        )

    # A later analysis updates the sample, while this job keeps its exact crop failures.
    requested_again = client.post(
        base + "/jobs", headers=headers(key=uuid.uuid4().hex), json=original
    )
    assert requested_again.status_code == 202, requested_again.text
    assert asyncio.run(worker.run_once())
    latest = client.get(
        base + "/analysis-jobs/" + requested_again.json()["job_id"], headers=headers()
    ).json()
    assert latest["state"] == "succeeded"
    assert all(report["status"] == "analyzed" for report in latest["quality"][0]["regions"])
    assert client.get(path, headers=headers()).json()["quality"] == quality


@pytest.mark.parametrize("forgery", ["unknown_region", "repeated_region"])
def test_manual_photo_forged_region_report_cannot_publish(capture_http, tmp_path, forgery):
    from app.core.errors import ApiError
    from app.modules.analysis.service import AnalysisArtifact, json_bytes
    from penforge_contracts import content_hash

    client, sync, _, _, _ = capture_http
    _, _, base, body = _verified_photo(capture_http, tmp_path)
    requested = client.post(base + "/jobs", headers=headers(key=uuid.uuid4().hex), json=body)
    assert requested.status_code == 202, requested.text
    service = client.app.state.analysis_service
    artifacts = []
    stage = service.stage

    async def collect(job, holder, produced):
        artifacts.extend(produced)
        return False

    async def scenario():
        job = await service.claim("region-forgery")
        assert job is not None
        service.stage = collect
        try:
            assert not await AnalysisWorker(
                service,
                worker_id="region-forgery",
                temp_root=tmp_path / "forged-analysis",
            ).execute(job)
        finally:
            service.stage = stage
        manifest = json.loads(artifacts[-1].data)
        report_item = artifacts[-2]
        report = json.loads(report_item.data)
        regions = report["samples"][0]["quality"]["regions"]
        if forgery == "unknown_region":
            regions[0]["region_id"] = str(uuid.uuid4())
        else:
            regions[1] = copy.deepcopy(regions[0])
        artifacts[-2] = AnalysisArtifact(
            report_item.artifact_id, report_item.kind, report_item.media_type, json_bytes(report)
        )
        manifest["quality_report"]["sha256"] = artifacts[-2].digest
        manifest["quality_report"]["size_bytes"] = len(artifacts[-2].data)
        manifest["artifact_hash"] = content_hash(manifest, exclude_root_keys={"artifact_hash"})
        manifest_item = artifacts[-1]
        artifacts[-1] = AnalysisArtifact(
            manifest_item.artifact_id,
            manifest_item.kind,
            manifest_item.media_type,
            json_bytes(manifest),
        )
        assert await service.stage(job, "region-forgery", artifacts)
        with pytest.raises(ApiError) as error:
            await service.publish(job, "region-forgery", manifest_item.artifact_id)
        assert error.value.code == "upload_mismatch"
        assert await service.fail(job, "region-forgery", "invalid_input")

    asyncio.run(scenario())
    with sync.connect() as con:
        assert con.scalar(sa.select(sa.func.count()).select_from(m.glyph_candidates)) == 0
        assert con.scalar(sa.select(sa.func.count()).select_from(m.ml_result_artifacts)) == 0
