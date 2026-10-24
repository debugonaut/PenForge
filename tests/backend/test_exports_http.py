"""Authenticated HTTP exports over real PostgreSQL; no hosted provider claim."""

import asyncio
import hashlib
import json
import uuid
from datetime import date
from pathlib import Path

import pytest
from app.db import models as m
from penforge_contracts import GlyphOutline
from sqlalchemy.dialects.postgresql import insert
from test_uploads_database import uploads_database as uploads_database
from test_uploads_http import capture_http as capture_http
from test_uploads_http import headers

from services.worker.exports import ExportWorker

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_http_identity_idempotency_download_expiry_and_content_free_logs(
    capture_http, tmp_path, capfd
):
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
        glyph = uuid.uuid4()
        con.execute(m.glyphs.insert().values(id=glyph, project_id=projects[0], codepoints=[65]))
    base = f"/api/v1/projects/{projects[0]}"
    outline = json.loads((ROOT / "packages/contracts/fixtures/outline.v1.json").read_text())
    outline["codepoints"] = [65]
    edited = client.patch(
        f"{base}/glyphs/{glyph}",
        headers={**headers(), "If-Match": '"v1"'},
        json={
            "action": "edit",
            "base_revision_id": None,
            "outline": GlyphOutline.model_validate(outline).model_dump(mode="json"),
        },
    )
    assert edited.status_code == 200, edited.text
    approved = client.patch(
        f"{base}/glyphs/{glyph}",
        headers={**headers(), "If-Match": edited.headers["ETag"]},
        json={"action": "approve", "base_revision_id": edited.json()["working_revision_id"]},
    )
    assert approved.status_code == 200
    body = {"formats": ["ttf"], "family_name": "PrivateSecretFamily"}
    assert client.post(base + "/exports", headers=headers(), json=body).status_code == 400
    key = uuid.uuid4().hex
    created = client.post(base + "/exports", headers=headers(key=key), json=body)
    assert created.status_code == 202, created.text
    assert (
        client.post(base + "/exports", headers=headers(key=key), json=body).json() == created.json()
    )
    eid = created.json()["export_ids"][0]
    for endpoint in [f"/api/v1/exports/{eid}", f"/api/v1/exports/{eid}/download-url"]:
        assert client.get(endpoint, headers=headers("other-token")).status_code == 404
    worker = ExportWorker(
        client.app.state.exports_service,
        worker_id="http-export",
        temp_root=tmp_path / "worker",
        heartbeat_seconds=0.05,
    )
    assert asyncio.run(worker.run_once())
    metadata = client.get(f"/api/v1/exports/{eid}", headers=headers()).json()
    assert metadata["status"] == "ready"
    download = client.get(f"/api/v1/exports/{eid}/download-url", headers=headers()).json()
    font = client.get(download["url"], headers=headers())
    assert font.status_code == 200 and font.content[:4] == b"\x00\x01\0\0"
    assert metadata["sha256"] == "sha256:" + hashlib.sha256(font.content).hexdigest()
    # Real expiry is enforced at the capability and live asset layer.
    import time
    from types import SimpleNamespace
    from unittest.mock import patch

    original = time.time
    with patch(
        "app.adapters.storage.local_fs.time", SimpleNamespace(time=lambda: original() + 10000)
    ):
        assert client.get(download["url"], headers=headers()).status_code == 404
    logs = capfd.readouterr().err
    assert (
        "PrivateSecretFamily" not in logs and download["url"] not in logs and "uni0041" not in logs
    )
