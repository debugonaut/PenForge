"""Real Firebase Auth emulator + PostgreSQL + public API acceptance.

Requires PENFORGE_TEST_AUTH_EMULATOR_HOST and TEST_DATABASE_URL. No real Firebase
project, credentials, email, Google network, handwriting or cloud resources are used.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.core.config import Settings
from app.db.session import normalize_database_url
from app.main import create_app
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.emulator]
PROJECT = "demo-penforge-identity"


@pytest.fixture
def emulator_app(tmp_path, monkeypatch) -> Iterator[tuple[TestClient, str]]:
    host = os.environ.get("PENFORGE_TEST_AUTH_EMULATOR_HOST")
    url = os.environ.get("TEST_DATABASE_URL")
    if not host or not url:
        pytest.skip("Real Auth emulator and PostgreSQL configuration required")
    assert host in ("127.0.0.1:9099", "localhost:9099")
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", host)
    admin = sa.create_engine(normalize_database_url(url))
    schema = "phase01_emulator_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    scoped_url = sa.make_url(normalize_database_url(url)).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    engine = sa.create_engine(scoped_url)
    config = Config(str(Path("infrastructure/migrations/alembic.ini").resolve()))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    settings = Settings(
        app_env="test",
        auth_backend="firebase",
        storage_backend="disabled",
        database_url=scoped_url.render_as_string(hide_password=False),
        firebase_project_id=PROJECT,
        firebase_allow_emulator=True,
        firebase_auth_emulator_host=host,
        identity_rate_limit=1000,
        identity_first_request_limit=100,
        identity_global_first_request_limit=1000,
    )
    try:
        with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
            yield client, "http://" + host
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def auth(base: str, action: str, **body) -> dict:
    result = httpx.post(
        f"{base}/identitytoolkit.googleapis.com/v1/accounts:{action}?key=emulator-only",
        json=body,
        timeout=10,
    )
    assert result.status_code == 200, "Emulator authentication request failed"
    return result.json()


def headers(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def test_anonymous_password_link_and_existing_account_claim(emulator_app):
    client, base = emulator_app
    anonymous = auth(base, "signUp", returnSecureToken=True)
    token = anonymous["idToken"]
    first = client.get("/api/v1/me", headers=headers(token))
    assert first.status_code == 200
    original = first.json()
    assert original["is_anonymous"] and original["tier"] == "free_guest"
    assert original["guest_creations"] == {
        "limit": 3,
        "used": 0,
        "remaining": 3,
        "applicable": True,
    }
    assert original["quotas"] == {"configured": False, "over_limit": False, "balances": []}
    assert client.get("/api/v1/me", headers=headers(token)).json()["user_id"] == original["user_id"]
    email = uuid.uuid4().hex + "@example.test"
    auth(
        base,
        "update",
        idToken=token,
        email=email,
        password="emulator-password-42",
        returnSecureToken=True,
    )
    linked = auth(
        base,
        "signInWithPassword",
        email=email,
        password="emulator-password-42",
        returnSecureToken=True,
    )
    upgraded = client.get("/api/v1/me", headers=headers(linked["idToken"]))
    assert upgraded.status_code == 200
    assert upgraded.json()["user_id"] == original["user_id"]
    assert upgraded.json()["is_anonymous"] is False
    assert upgraded.json()["tier"] == "free_user"
    # The old anonymous claim remains valid cryptographically but cannot downgrade state.
    stale = client.get("/api/v1/me", headers=headers(token))
    assert stale.status_code == 401
    guest = auth(base, "signUp", returnSecureToken=True)
    assert client.get("/api/v1/me", headers=headers(guest["idToken"])).status_code == 200
    claim = client.post(
        "/api/v1/identity/claim",
        headers=headers(linked["idToken"]),
        json={"guest_id_token": guest["idToken"]},
    )
    assert claim.status_code == 200
    assert claim.json()["mode"] == "transferred"
    replay = client.post(
        "/api/v1/identity/claim",
        headers=headers(linked["idToken"]),
        json={"guest_id_token": guest["idToken"]},
    )
    assert replay.json() == claim.json()
    assert (
        client.get("/api/v1/me", headers=headers(guest["idToken"])).json()["error"]["code"]
        == "account_merged"
    )


def test_emulated_google_link_keeps_uid_and_revocation_blocks_access(emulator_app):
    client, base = emulator_app
    guest = auth(base, "signUp", returnSecureToken=True)
    before = client.get("/api/v1/me", headers=headers(guest["idToken"])).json()
    identity = {
        "sub": uuid.uuid4().hex,
        "email": uuid.uuid4().hex + "@example.test",
        "email_verified": True,
    }
    import json

    linked = auth(
        base,
        "signInWithIdp",
        idToken=guest["idToken"],
        requestUri="http://localhost",
        returnSecureToken=True,
        postBody=urlencode({"providerId": "google.com", "id_token": json.dumps(identity)}),
    )
    after = client.get("/api/v1/me", headers=headers(linked["idToken"]))
    assert after.status_code == 200
    assert after.json()["user_id"] == before["user_id"]
    assert after.json()["sign_in_provider"] == "google.com"
    assert after.json()["email_verified"] is True
    # Emulator supports disabled-user lookup through the real Admin SDK revocation path.
    from firebase_admin import auth as admin_auth

    verifier = client.app.state.auth_verifier
    admin_auth.update_user(linked["localId"], disabled=True, app=verifier._app)
    denied = client.get("/api/v1/me", headers=headers(linked["idToken"]))
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "invalid_token"


def test_cloud_project_quota_replay_and_claimed_owner_preserve_guest_work(emulator_app):
    client, base = emulator_app
    guest = auth(base, "signUp", returnSecureToken=True)
    guest_headers = headers(guest["idToken"])
    assert client.get("/api/v1/me", headers=guest_headers).status_code == 200
    created = []
    for index in range(3):
        response = client.post(
            "/api/v1/projects",
            headers={**guest_headers, "Idempotency-Key": f"emulator-project-{index}"},
            json={"name": f"Guest project {index}", "script_code": "Latn"},
        )
        assert response.status_code == 201
        assert response.headers["ETag"] == '"v1"'
        created.append(response.json())

    replay = client.post(
        "/api/v1/projects",
        headers={**guest_headers, "Idempotency-Key": "emulator-project-0"},
        json={"name": "Guest project 0", "script_code": "Latn"},
    )
    assert replay.status_code == 201
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == created[0]
    blocked = client.post(
        "/api/v1/projects",
        headers={**guest_headers, "Idempotency-Key": "emulator-project-4"},
        json={"name": "Fourth project", "script_code": "Latn"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "guest_limit_reached"
    assert client.get("/api/v1/me", headers=guest_headers).json()["guest_creations"]["used"] == 3

    email = uuid.uuid4().hex + "@example.test"
    destination = auth(
        base,
        "signUp",
        email=email,
        password="emulator-password-42",
        returnSecureToken=True,
    )
    destination_headers = headers(destination["idToken"])
    claim = client.post(
        "/api/v1/identity/claim",
        headers=destination_headers,
        json={"guest_id_token": guest["idToken"]},
    )
    assert claim.status_code == 200
    assert claim.json()["transferred"]["projects"] == 3
    replayed_claim = client.post(
        "/api/v1/identity/claim",
        headers=destination_headers,
        json={"guest_id_token": guest["idToken"]},
    )
    assert replayed_claim.json() == claim.json()

    fetched = client.get(f"/api/v1/projects/{created[0]['id']}", headers=destination_headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Guest project 0"
    updated = client.patch(
        f"/api/v1/projects/{created[0]['id']}",
        headers={**destination_headers, "If-Match": '"v1"'},
        json={"name": "Claimed project"},
    )
    assert updated.status_code == 200
    assert updated.headers["ETag"] == '"v2"'
    stale_guest = client.patch(
        f"/api/v1/projects/{created[0]['id']}",
        headers={**guest_headers, "If-Match": '"v1"'},
        json={"name": "stale guest overwrite"},
    )
    assert stale_guest.status_code == 403
    assert stale_guest.json()["error"]["code"] == "account_merged"


def test_errors_cors_rate_headers_and_secret_redaction(emulator_app, caplog):
    client, base = emulator_app
    client.app.state.settings.identity_rate_limit = 2
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers=headers("PRIVATE_FORGED_TOKEN")).status_code == 401
    token = auth(base, "signUp", returnSecureToken=True)["idToken"]
    # Previous forged request consumed one admission in this fixture.
    valid = client.get("/api/v1/me", headers=headers(token))
    assert valid.status_code == 200
    limited = client.get("/api/v1/me", headers=headers(token))
    assert limited.status_code == 429
    for name in ("Retry-After", "RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset"):
        assert name in limited.headers
    preflight = client.options(
        "/api/v1/identity/claim",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert "PRIVATE_FORGED_TOKEN" not in caplog.text and token not in caplog.text
