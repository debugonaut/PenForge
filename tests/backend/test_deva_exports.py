"""Real owner review, immutable snapshots, export validation and erasure for Deva."""

import asyncio
import uuid
from datetime import date

import pytest
from app.db import models as m
from sqlalchemy.dialects.postgresql import insert
from test_uploads_database import uploads_database as uploads_database
from test_uploads_http import capture_http as capture_http
from test_uploads_http import headers

from scripts.deva_fixture import fixture
from services.worker.exports import ExportWorker

pytestmark = pytest.mark.integration


def test_deva_review_three_real_formats_and_equivalent_requests(capture_http, tmp_path):
    client, sync, users, projects, uploads = capture_http
    manifest, rows = fixture()
    with sync.begin() as c:
        c.execute(
            m.projects.update().where(m.projects.c.id == projects[0]).values(script_code="Deva")
        )
        c.execute(m.quota_balances.update().values(limit_amount=100_000_000))
        for resource in ("exports", "compute_seconds"):
            for scope in ("user", "global"):
                c.execute(
                    insert(m.quota_balances)
                    .values(
                        id=uuid.uuid4(),
                        scope=scope,
                        user_id=users[0] if scope == "user" else None,
                        resource=resource,
                        window_kind="lifetime",
                        window_start=date(1970, 1, 1),
                        limit_amount=100000,
                    )
                    .on_conflict_do_nothing()
                )
    base = f"/api/v1/projects/{projects[0]}"
    for row in rows:
        gid = uuid.uuid4()
        o = row["outline"]
        with sync.begin() as c:
            c.execute(
                m.glyphs.insert().values(id=gid, project_id=projects[0], codepoints=o["codepoints"])
            )
        edit = client.patch(
            f"{base}/glyphs/{gid}",
            headers={**headers(), "If-Match": '"v1"'},
            json=dict(action="edit", base_revision_id=None, outline=o),
        )
        assert edit.status_code == 200, edit.text
        approve = client.patch(
            f"{base}/glyphs/{gid}",
            headers={**headers(), "If-Match": edit.headers["ETag"]},
            json=dict(action="approve", base_revision_id=edit.json()["working_revision_id"]),
        )
        assert approve.status_code == 200, approve.text
    counts = client.get(base + "/coverage", headers=headers()).json()
    assert counts["script_details"]["shaping_validation_state"] == "unverified"
    prompt = client.get(base + "/calibration/next", headers=headers())
    assert prompt.status_code == 200
    body = dict(
        formats=["ttf", "otf", "woff2"],
        family_name="Deva Engineering Fixture",
        options=manifest.options.model_dump(mode="json"),
    )
    first = client.post(base + "/exports", headers=headers(key=uuid.uuid4().hex), json=body)
    assert first.status_code == 202, first.text
    worker = ExportWorker(
        client.app.state.exports_service, worker_id="deva-export", temp_root=tmp_path / "worker"
    )
    assert asyncio.run(worker.run_once())

    def outputs(receipt):
        values = []
        for eid in receipt["export_ids"]:
            path = f"/api/v1/exports/{eid}"
            assert client.get(path, headers=headers("other-token")).status_code == 404
            metadata = client.get(path, headers=headers()).json()
            assert metadata["status"] == "ready", metadata
            coverage = metadata["coverage_report"]
            assert coverage["script_code"] == "Deva" and not coverage["complete_devanagari"]
            assert len(coverage["tested_clusters"]) + len(coverage["fallback_only_clusters"]) == 11
            link = client.get(path + "/download-url", headers=headers()).json()
            data = client.get(link["url"])
            assert data.status_code == 200
            values.append(data.content)
        return values

    a = outputs(first.json())
    body["snapshot_id"] = first.json()["snapshot_id"]
    second = client.post(base + "/exports", headers=headers(key=uuid.uuid4().hex), json=body)
    assert second.status_code == 202, second.text
    assert asyncio.run(worker.run_once())
    assert outputs(second.json()) == a
