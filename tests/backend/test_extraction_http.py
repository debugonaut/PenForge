"""HTTP contracts and authorization over real PostgreSQL services."""

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from test_uploads_database import uploads_database as uploads_database
from test_uploads_http import capture_http as capture_http
from test_uploads_http import headers

from services.worker.analysis import AnalysisWorker

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_public_pinned_template_routes_have_real_print_files(capture_http):
    client, *_ = capture_http
    catalog = client.get("/api/v1/templates")
    assert catalog.status_code == 200
    assert len(catalog.json()["items"]) == 2
    for item in catalog.json()["items"]:
        assert item["manifest"]["printing"]["printable_margin_mm"] == 8.0
        assert client.get(item["svg_url"]).text.startswith("<svg")
        assert client.get(item["pdf_url"]).content.startswith(b"%PDF")
    assert client.get("/api/v1/templates/unknown/1.0.0/0.pdf").status_code == 404


def test_actual_http_drawing_analysis_selection_edit_approval_snapshot(capture_http, tmp_path):
    client, sync, users, projects, uploads = capture_http
    uploads.upload_max_bytes = 20_000
    base = "/api/v1/projects/" + str(projects[0])
    document = json.loads((ROOT / "packages/contracts/fixtures/strokes.v1.json").read_text())
    saved = client.post(
        base + "/samples",
        headers=headers(key=uuid.uuid4().hex),
        json=dict(source_kind="strokes", stroke_document=document),
    )
    assert saved.status_code == 201, saved.text
    sample = saved.json()
    assert (
        sample["capture_size_px"] == document["capture_size_px"] and sample["timing"] == "observed"
    )
    body = dict(
        job_type="analyze_samples",
        sample_ids=[sample["sample_id"]],
        assignments=[
            dict(
                sample_id=sample["sample_id"],
                codepoints=[103],
                capture_transform=dict(baseline_y=0.75, units_per_px=1.0, pen_width_px=3.0),
            )
        ],
    )
    key = uuid.uuid4().hex
    requested = client.post(base + "/jobs", headers=headers(key=key), json=body)
    assert requested.status_code == 202, requested.text
    replay = client.post(base + "/jobs", headers=headers(key=key), json=body)
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert (
        client.post(
            base + "/jobs", headers=headers("other-token", uuid.uuid4().hex), json=body
        ).status_code
        == 404
    )
    service = client.app.state.analysis_service
    assert asyncio.run(
        AnalysisWorker(service, worker_id="http", temp_root=tmp_path / "attempts").run_once()
    )
    result = client.get(base + "/analysis-jobs/" + requested.json()["job_id"], headers=headers())
    assert result.json()["state"] == "succeeded", result.text
    stored = client.get(base + "/samples", headers=headers()).json()["items"][0]
    assert (
        stored["state"] == "analyzed" and stored["capture_size_px"] == document["capture_size_px"]
    )
    glyph = client.get(base + "/glyphs", headers=headers()).json()["items"][0]
    path = base + "/glyphs/" + glyph["id"]
    detail = client.get(path, headers=headers())
    assert detail.headers["ETag"] == '"v1"'
    alternatives = client.get(path + "/candidates", headers=headers()).json()["items"]
    select_body = dict(candidate_id=alternatives[0]["id"], base_revision_id=None)
    missing = client.post(
        path + "/select-candidate", headers=headers(key=uuid.uuid4().hex), json=select_body
    )
    assert missing.status_code == 428
    selected = client.post(
        path + "/select-candidate",
        headers={**headers(key=uuid.uuid4().hex), "If-Match": detail.headers["ETag"]},
        json=select_body,
    )
    assert selected.status_code == 200 and selected.json()["active_revision_id"] is None
    edited = json.loads(json.dumps(selected.json()["outline"]))
    edited["metrics"]["advance_width"] += 10
    pending = client.patch(
        path,
        headers={**headers(), "If-Match": selected.headers["ETag"]},
        json=dict(
            action="edit", base_revision_id=selected.json()["working_revision_id"], outline=edited
        ),
    )
    assert pending.status_code == 200, pending.text
    approved = client.patch(
        path,
        headers={**headers(), "If-Match": pending.headers["ETag"]},
        json=dict(action="approve", base_revision_id=pending.json()["working_revision_id"]),
    )
    assert (
        approved.status_code == 200
        and approved.json()["active_revision_id"] == pending.json()["working_revision_id"]
    )
    assert (
        client.patch(
            path,
            headers={**headers(), "If-Match": pending.headers["ETag"]},
            json=dict(action="approve", base_revision_id=pending.json()["working_revision_id"]),
        ).status_code
        == 412
    )
    snapshot = client.post(
        base + "/snapshots",
        headers=headers(key=uuid.uuid4().hex),
        json=dict(kind="milestone", label="HTTP"),
    )
    assert snapshot.status_code == 201, snapshot.text
    snapshot_id = snapshot.json()["id"]
    assert (
        client.get(base + "/snapshots/" + snapshot_id, headers=headers()).json() == snapshot.json()
    )
    for resource in (
        "/glyphs",
        "/glyphs/" + glyph["id"],
        "/glyphs/" + glyph["id"] + "/candidates",
        "/coverage",
        "/capture-prompts",
        "/calibration/next",
        "/snapshots/" + snapshot_id,
        "/analysis-jobs/" + requested.json()["job_id"],
    ):
        assert client.get(base + resource).status_code == 401
        assert client.get(base + resource, headers=headers("other-token")).status_code == 404
    assert (
        client.get(base + "/coverage", headers=headers()).json()["complete_font_validated"] is False
    )
