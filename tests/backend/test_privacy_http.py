"""Privacy REST contracts against real PostgreSQL and injected verified identities."""

import pytest
from app.modules.privacy.service import PrivacyService
from test_uploads_database import uploads_database as uploads_database
from test_uploads_http import capture_http as capture_http
from test_uploads_http import headers

pytestmark = pytest.mark.integration


def attach(client, uploads):
    service = PrivacyService(
        uploads.engine,
        uploads.identity_service,
        uploads,
        uploads.storage,
        b"privacy-http-test-secret" * 2,
    )
    client.app.state.privacy_service = service
    return service


def test_explicit_consent_and_owner_only_deletion_progress(capture_http):
    client, sync, users, projects, uploads = capture_http
    attach(client, uploads)
    assert client.get("/api/v1/consent", headers=headers()).json()["granted"] is False
    assert (
        client.put(
            "/api/v1/consent",
            headers=headers(),
            json={"purpose": "other", "granted": True, "policy_version": "model-training-v1"},
        ).status_code
        == 400
    )
    grant = client.put(
        "/api/v1/consent",
        headers=headers(),
        json={"purpose": "model_training", "granted": True, "policy_version": "model-training-v1"},
    )
    assert grant.status_code == 200 and grant.json()["granted"] is True
    assert (
        client.post(
            "/api/v1/privacy/deletion-requests",
            headers=headers(),
            json={"scope": "project", "confirmed": True},
        ).status_code
        == 400
    )
    body = {
        "scope": "project",
        "project_id": str(projects[0]),
        "confirmed": True,
        "expected_project_version": 1,
    }
    assert (
        client.post("/api/v1/privacy/deletion-requests", headers=headers(), json=body).status_code
        == 400
    )
    created = client.post(
        "/api/v1/privacy/deletion-requests",
        headers=headers() | {"Idempotency-Key": "privacy"},
        json=body,
    )
    assert created.status_code == 202, created.text
    record = created.json()
    path = "/api/v1/privacy/deletion-requests/" + record["id"]
    assert client.get(path, headers=headers("other-token")).status_code == 404
    assert client.get(path, headers={"X-Deletion-Receipt": "f" * 64}).status_code == 404
    assert (
        client.get(path, headers={"X-Deletion-Receipt": record["progress_token"]}).status_code
        == 200
    )
    assert client.get(path).status_code == 401
    replay = client.post(
        "/api/v1/privacy/deletion-requests",
        headers=headers() | {"Idempotency-Key": "privacy"},
        json=body,
    )
    assert replay.json() == record
    assert client.get(f"/api/v1/projects/{projects[0]}/samples", headers=headers()).status_code in (
        404,
        409,
    )


def test_existing_delete_route_adapts_to_same_saga_and_keeps_etag(capture_http):
    client, sync, users, projects, uploads = capture_http
    attach(client, uploads)
    path = f"/api/v1/projects/{projects[0]}"
    assert client.delete(path, headers=headers()).status_code == 428
    assert client.delete(path, headers=headers() | {"If-Match": '"v99"'}).status_code == 412
    result = client.delete(path, headers=headers() | {"If-Match": '"v1"'})
    assert result.status_code == 202, result.text
    assert result.json()["scope"] == "project"
    assert (
        client.delete(path, headers=headers() | {"If-Match": '"v1"'}).json()["id"]
        == result.json()["id"]
    )
