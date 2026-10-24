"""Real PostgreSQL HTTP capture authorization, capability and body-limit regressions.

Firebase verification is injected with synthetic verified principals. No hosted
provider, credential, decoder quality or cloud storage behavior is implied.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from app.adapters.auth.base import Principal
from app.adapters.storage.local_fs import LocalObjectStorage
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.logging import SafeJsonFormatter, logger
from app.db import models as m
from app.main import create_app
from app.modules.identity.service import IdentityService
from app.modules.uploads.service import UploadsService
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from test_uploads_database import uploads_database  # noqa: F401

pytestmark = pytest.mark.integration
SOURCE = b"transport fixture"


class VerifiedPrincipals:
    def __init__(self, principals: dict[str, Principal]):
        self.principals = principals

    async def verify(self, token: str, *, check_revoked: bool = False) -> Principal:
        if not check_revoked or token not in self.principals:
            raise ApiError("invalid_token", 401)
        return self.principals[token]


@pytest.fixture
def capture_http(uploads_database, tmp_path):  # noqa: F811 - imported pytest fixture
    sync, schema = uploads_database
    users = [uuid.uuid4(), uuid.uuid4()]
    projects = [uuid.uuid4(), uuid.uuid4()]
    principals = {}
    with sync.begin() as connection:
        for token, user, project in zip(
            ("owner-token", "other-token"), users, projects, strict=True
        ):
            uid = "http-" + uuid.uuid4().hex
            principals[token] = Principal(
                user_id=None,
                firebase_uid=uid,
                is_anonymous=False,
                sign_in_provider="password",
                email_verified=True,
                auth_time=int(time.time()),
                token_issued_at=int(time.time()),
            )
            connection.execute(
                m.users.insert().values(
                    id=user, firebase_uid=uid, is_anonymous=False, tier="free_user"
                )
            )
            connection.execute(
                m.projects.insert().values(
                    id=project, owner_user_id=user, name="HTTP capture", script_code="Latn"
                )
            )
            connection.execute(
                m.quota_balances.insert().values(
                    id=uuid.uuid4(),
                    scope="user",
                    user_id=user,
                    resource="storage_bytes",
                    window_kind="lifetime",
                    window_start=date(1970, 1, 1),
                    limit_amount=1_000_000,
                )
            )
        connection.execute(
            m.quota_balances.insert().values(
                id=uuid.uuid4(),
                scope="global",
                resource="storage_bytes",
                window_kind="lifetime",
                window_start=date(1970, 1, 1),
                limit_amount=2_000_000,
            )
        )
    with sync.begin() as connection:
        for user in [None, *users]:
            connection.execute(
                m.quota_balances.insert().values(
                    id=uuid.uuid4(),
                    scope="user" if user else "global",
                    user_id=user,
                    resource="compute_seconds",
                    window_kind="lifetime",
                    window_start=date(1970, 1, 1),
                    limit_amount=10000,
                )
            )
    engine = create_async_engine(
        sync.url, connect_args={"options": f"-csearch_path={schema}"}, poolclass=NullPool
    )
    storage = LocalObjectStorage(
        tmp_path / "injected-objects", secret=b"s" * 32, max_upload_bytes=1024
    )
    identity = IdentityService(engine)
    uploads = UploadsService(
        engine, identity, storage, upload_max_bytes=1024, derivative_max_bytes=1024
    )
    settings = Settings(
        app_env="test",
        auth_backend="firebase",
        firebase_project_id="penforge-test",
        storage_root=tmp_path / "app-objects",
        upload_max_bytes=1024,
        identity_rate_limit=1000,
        hmac_secret="s" * 32,
    )
    app = create_app(
        settings,
        auth_verifier=VerifiedPrincipals(principals),
        identity_service=identity,
        uploads_service=uploads,
    )
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            policy = client.get("/api/v1/service-policy").json()
            for token in principals:
                accepted = client.post(
                    "/api/v1/me/service-agreements",
                    headers={"Authorization": "Bearer " + token},
                    json={
                        "policy_sha256": policy["sha256"],
                        "terms_accepted": True,
                        "handwriting_rights_confirmed": True,
                    },
                )
                assert accepted.status_code == 200, accepted.text
            yield client, sync, users, projects, uploads
    finally:
        storage.close()
        asyncio.run(engine.dispose())


def headers(token="owner-token", key: str | None = None):
    result = {"Authorization": "Bearer " + token}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def initialize(client, project, *, token="owner-token", data=SOURCE, key=None):
    key = key or uuid.uuid4().hex
    body = {
        "filename": "private-file.png",
        "declared_mime": "image/png",
        "declared_size": len(data),
        "source_type": "freeform_photo",
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
    }
    response = client.post(
        f"/api/v1/projects/{project}/uploads", headers=headers(token, key), json=body
    )
    assert response.status_code == 201, response.text
    return response.json(), body, key


def ready(sync, asset_id, data):
    aid = uuid.UUID(asset_id)
    with sync.begin() as connection:
        connection.execute(m.assets.update().where(m.assets.c.id == aid).values(state="uploaded"))
        connection.execute(m.assets.update().where(m.assets.c.id == aid).values(state="verifying"))
        connection.execute(
            m.assets.update()
            .where(m.assets.c.id == aid)
            .values(
                state="ready", sha256=hashlib.sha256(data).digest(), verified_at=datetime.now(UTC)
            )
        )


def test_all_capture_resource_routes_require_authentication_and_hide_foreign_ids(capture_http):
    client, sync, users, projects, uploads = capture_http
    del sync, users, uploads
    created, _, _ = initialize(client, projects[0])
    aid = created["asset_id"]
    assert (
        client.put(created["upload"]["url"], content=SOURCE, headers=headers()).status_code == 204
    )
    complete = client.post(f"/api/v1/uploads/{aid}/complete", headers=headers())
    assert complete.status_code == 202
    job = complete.json()["job_id"]
    sample = {"source_kind": "asset", "asset_id": aid, "source_type": "freeform_photo"}
    init_body = {
        "filename": "capture.png",
        "declared_mime": "image/png",
        "declared_size": 1,
        "source_type": "freeform_photo",
    }
    routes = [
        ("POST", f"/api/v1/projects/{projects[0]}/uploads", init_body),
        ("POST", f"/api/v1/uploads/{aid}/upload-url", None),
        ("POST", f"/api/v1/uploads/{aid}/complete", None),
        ("GET", f"/api/v1/assets/{aid}", None),
        ("GET", f"/api/v1/assets/{aid}/download-url", None),
        ("POST", f"/api/v1/projects/{projects[0]}/samples", sample),
        ("GET", f"/api/v1/projects/{projects[0]}/samples", None),
        ("GET", f"/api/v1/jobs/{job}", None),
    ]
    for method, path, body in routes:
        unauth = client.request(
            method, path, headers={"Idempotency-Key": uuid.uuid4().hex}, json=body
        )
        assert unauth.status_code == 401, (path, unauth.text)
        foreign = client.request(
            method, path, headers=headers("other-token", uuid.uuid4().hex), json=body
        )
        assert foreign.status_code == 404, (path, foreign.text)
        assert "storage_key" not in foreign.text and "lease_holder" not in foreign.text
    # The attacker owns this project; the source ID still does not grant access.
    foreign_source = client.post(
        f"/api/v1/projects/{projects[1]}/samples",
        headers=headers("other-token", uuid.uuid4().hex),
        json=sample,
    )
    assert foreign_source.status_code == 404
    polling = client.get(f"/api/v1/jobs/{job}", headers=headers())
    assert polling.status_code == 200 and polling.json()["eta_seconds"] is None
    assert (
        polling.json()["state"] == "queued" and polling.json()["progress"]["completed_units"] == 0
    )
    assert polling.headers["cache-control"] == "no-store"
    asset = client.get(f"/api/v1/assets/{aid}", headers=headers())
    assert asset.status_code == 200 and "storage_key" not in asset.text
    assert client.get(created["upload"]["url"], headers=headers()).status_code == 404
    assert (
        client.put(created["upload"]["url"], content=SOURCE, headers=headers()).status_code == 404
    )


def test_upload_and_download_capabilities_rotate_expire_and_do_not_enter_application_logs(
    capture_http,
    monkeypatch,
):
    from types import SimpleNamespace

    from app.adapters.storage import local_fs

    # Local capabilities use integer epoch expiry. A one-second real-time TTL
    # could expire between validation and reading when a request crosses a second.
    observed_time = [time.time()]
    monkeypatch.setattr(local_fs, "time", SimpleNamespace(time=lambda: observed_time[0]))
    client, sync, users, projects, uploads = capture_http
    del users
    uploads.signed_upload_ttl_seconds = 1
    created, _, _ = initialize(client, projects[0])
    aid = created["asset_id"]
    old = created["upload"]["url"]
    renewed = client.post(f"/api/v1/uploads/{aid}/upload-url", headers=headers())
    assert renewed.status_code == 200
    current = renewed.json()["upload"]["url"]
    assert old != current and client.put(old, content=SOURCE, headers=headers()).status_code == 404
    assert client.put(current, content=SOURCE, headers=headers()).status_code == 204
    observed_time[0] += 2
    assert client.put(current, content=SOURCE, headers=headers()).status_code == 404
    ready(sync, aid, SOURCE)
    uploads.signed_download_ttl_seconds = 1
    first = client.get(f"/api/v1/assets/{aid}/download-url", headers=headers()).json()["url"]
    second = client.get(f"/api/v1/assets/{aid}/download-url", headers=headers()).json()["url"]
    assert client.get(first, headers=headers()).status_code == 404
    observed = []

    class Capture(logging.Handler):
        def emit(self, record):
            observed.append(SafeJsonFormatter().format(record))

    handler = Capture()
    logger.addHandler(handler)
    try:
        download = client.get(second, headers=headers())
        assert download.status_code == 200 and download.content == SOURCE
        assert (
            download.headers["cache-control"] == "no-store"
            and download.headers["x-content-type-options"] == "nosniff"
        )
        assert client.put(second, content=SOURCE, headers=headers()).status_code == 404
    finally:
        logger.removeHandler(handler)
    serialized = "\n".join(observed)
    assert (
        second.rsplit("/", 1)[1] not in serialized
        and "owner-token" not in serialized
        and SOURCE.decode() not in serialized
    )
    observed_time[0] += 2
    assert client.get(second, headers=headers()).status_code == 404


def test_exact_and_chunked_upload_byte_limits_and_sample_schema_validation(capture_http):
    client, sync, users, projects, uploads = capture_http
    del sync, users, uploads
    data = b"x" * 1024
    created, _, _ = initialize(client, projects[0], data=data)
    exact = client.put(
        created["upload"]["url"], content=iter([data[:512], data[512:]]), headers=headers()
    )
    assert exact.status_code == 204, exact.text
    oversize = client.put(created["upload"]["url"], content=iter([data, b"x"]), headers=headers())
    assert oversize.status_code == 413 and oversize.json()["error"]["code"] == "payload_too_large"
    wrong_size = client.put(created["upload"]["url"], content=data[:-1], headers=headers())
    assert wrong_size.status_code == 400 and wrong_size.json()["error"]["code"] == "upload_mismatch"
    bad = {
        "source_kind": "strokes",
        "stroke_document": {
            "schema_version": 77,
            "private_text": "private handwriting must not be echoed",
        },
    }
    rejected = client.post(
        f"/api/v1/projects/{projects[0]}/samples", headers=headers(key=uuid.uuid4().hex), json=bad
    )
    assert rejected.status_code == 400 and "private handwriting" not in rejected.text
    assert (
        client.put(
            "/api/v1/storage/objects/not-a-capability", content=b"small", headers=headers()
        ).status_code
        == 404
    )


def test_rollback_pauses_new_issuance_but_allows_original_replay_renewal_and_completion(
    capture_http,
):
    client, sync, users, projects, uploads = capture_http
    created, body, key = initialize(client, projects[0])
    aid = created["asset_id"]
    uploads.upload_issuance_enabled = False
    replay = client.post(
        f"/api/v1/projects/{projects[0]}/uploads", headers=headers(key=key), json=body
    )
    assert replay.status_code == 201 and replay.json() == created
    assert replay.headers["idempotency-replayed"] == "true"
    blocked = client.post(
        f"/api/v1/projects/{projects[0]}/uploads", headers=headers(key=uuid.uuid4().hex), json=body
    )
    assert (
        blocked.status_code == 503 and blocked.json()["error"]["code"] == "upload_issuance_disabled"
    )
    renewed = client.post(f"/api/v1/uploads/{aid}/upload-url", headers=headers())
    assert renewed.status_code == 200
    assert (
        client.put(renewed.json()["upload"]["url"], content=SOURCE, headers=headers()).status_code
        == 204
    )
    completed = client.post(f"/api/v1/uploads/{aid}/complete", headers=headers())
    assert completed.status_code == 202
    with sync.connect() as connection:
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(m.assets)
                .where(m.assets.c.owner_user_id == users[0])
            )
            == 1
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(m.idempotency_keys)
                .where(m.idempotency_keys.c.user_id == users[0])
            )
            == 1
        )
        reserved = connection.scalar(
            sa.select(m.quota_balances.c.reserved).where(m.quota_balances.c.user_id == users[0])
        )
        assert reserved == len(SOURCE) + 1024
