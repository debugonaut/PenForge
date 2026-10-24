"""HTTP contracts and policy for metadata-only cloud projects."""

import json
from datetime import UTC, datetime
from uuid import UUID

from app.adapters.auth.base import Principal
from app.core.config import Settings
from app.core.errors import ApiError
from app.main import create_app
from fastapi.testclient import TestClient

PROJECT_ID = UUID("33333333-3333-4333-8333-333333333333")


class Verifier:
    async def verify(self, token: str, *, check_revoked: bool = False) -> Principal:
        if token != "owner-token" or not check_revoked:
            raise ApiError("invalid_token", 401)
        return Principal(
            user_id=None,
            firebase_uid="owner-uid",
            is_anonymous=False,
            sign_in_provider="password",
            email_verified=True,
            auth_time=100,
            token_issued_at=100,
        )


class Identity:
    async def identity_exists(self, uid: str) -> bool:
        return True


class AcceptedService:
    async def access(self, *args, **kwargs):
        return {"eligible": True}


class Projects:
    def __init__(self):
        self.created = []
        self.updated = []
        self.project = {
            "id": str(PROJECT_ID),
            "name": "Cloud draft",
            "script_code": "Latn",
            "status": "draft",
            "active_snapshot_id": None,
            "style_controls": {"thickness": 55.0},
            "row_version": 1,
            "created_at": datetime(2026, 10, 19, tzinfo=UTC),
            "updated_at": datetime(2026, 10, 19, tzinfo=UTC),
        }

    async def create(self, principal, *, idempotency_key, name, script_code, style_controls):
        self.created.append((principal, idempotency_key, name, script_code, style_controls))
        return (
            False,
            201,
            {
                **self.project,
                "name": name,
                "script_code": script_code,
                "style_controls": style_controls,
            },
        )

    async def list(self, principal, *, limit, cursor):
        return {"items": [self.project], "next_cursor": None}

    async def get(self, principal, project_id):
        if project_id != PROJECT_ID:
            raise ApiError("not_found", 404)
        return self.project

    async def update(self, principal, project_id, *, if_match, changes):
        if project_id != PROJECT_ID:
            raise ApiError("not_found", 404)
        if if_match is None:
            raise ApiError("precondition_required", 428)
        if if_match != '"v1"':
            raise ApiError("revision_conflict", 412)
        self.updated.append((if_match, changes))
        return {**self.project, **changes, "row_version": 2}

    async def request_delete(self, principal, project_id, *, if_match):
        if project_id != PROJECT_ID:
            raise ApiError("not_found", 404)
        if if_match is None:
            raise ApiError("precondition_required", 428)
        if if_match != '"v1"':
            raise ApiError("revision_conflict", 412)
        raise ApiError("capability_unavailable", 503)


def client_with_projects(tmp_path):
    projects = Projects()
    settings = Settings(
        app_env="test",
        storage_root=tmp_path / "objects",
        auth_backend="firebase",
        firebase_project_id="penforge-test",
        cors_allowed_origins=["http://localhost:3000"],
    )
    app = create_app(
        settings,
        auth_verifier=Verifier(),
        identity_service=Identity(),
        projects_service=projects,
        accountability_service=AcceptedService(),
    )
    return TestClient(app, client=("127.0.0.1", 50000)), projects


def auth_headers(**headers):
    return {"authorization": "Bearer owner-token", **headers}


def test_project_http_etags_idempotency_and_if_match_headers_are_explicit(tmp_path):
    with client_with_projects(tmp_path)[0] as client:
        created = client.post(
            "/api/v1/projects",
            headers=auth_headers(**{"Idempotency-Key": "http-create-1"}),
            json={
                "name": "  Cloud draft  ",
                "script_code": "Latn",
                "style_controls": {"thickness": 55},
            },
        )
        assert created.status_code == 201, created.text
        assert created.headers["etag"] == '"v1"'
        assert created.headers["cache-control"] == "no-store"
        fetched = client.get(f"/api/v1/projects/{PROJECT_ID}", headers=auth_headers())
        assert fetched.status_code == 200
        assert fetched.headers["etag"] == '"v1"'
        listed = client.get("/api/v1/projects?limit=10", headers=auth_headers())
        assert listed.status_code == 200 and len(listed.json()["items"]) == 1
        changed = client.patch(
            f"/api/v1/projects/{PROJECT_ID}",
            headers=auth_headers(**{"If-Match": '"v1"'}),
            json={"name": "Updated", "style_controls": {"slant": -5}},
        )
        assert changed.status_code == 200
        assert changed.headers["etag"] == '"v2"'
        assert changed.json()["style_controls"] == {"slant": -5}
        missing = client.patch(
            f"/api/v1/projects/{PROJECT_ID}",
            headers=auth_headers(),
            json={"name": "Missing precondition"},
        )
        assert missing.status_code == 428
        assert missing.json()["error"]["code"] == "precondition_required"
        schema = client.get("/openapi.json").json()
        patch = schema["paths"]["/api/v1/projects/{project_id}"]["patch"]
        if_match = next(item for item in patch["parameters"] if item["name"] == "If-Match")
        assert if_match["required"] is True
        assert "ETag" in patch["responses"]["200"]["headers"]
        delete = schema["paths"]["/api/v1/projects/{project_id}"]["delete"]
        delete_if_match = next(item for item in delete["parameters"] if item["name"] == "If-Match")
        assert delete_if_match["required"] is True
        assert "202" in delete["responses"]
        assert delete["responses"]["202"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "DeletionView"
        )
        assert "503" in delete["responses"]
        post = schema["paths"]["/api/v1/projects"]["post"]
        assert "Idempotency-Replayed" in post["responses"]["201"]["headers"]


def test_project_validation_delete_capability_and_foreign_errors_are_safe(tmp_path):
    app_client, projects = client_with_projects(tmp_path)
    with app_client as client:
        invalid = client.post(
            "/api/v1/projects",
            headers=auth_headers(**{"Idempotency-Key": "invalid-style"}),
            json={"name": "No", "style_controls": {"thickness": 101}},
        )
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "validation_error"
        # A random request UUID may contain 101; inspect the error payload instead.
        safe_error = invalid.json()["error"].copy()
        safe_error.pop("request_id", None)
        assert "101" not in json.dumps(safe_error)
        for value in ("55", True, float("nan")):
            body = json.dumps(
                {"name": "No", "style_controls": {"thickness": value}},
                allow_nan=True,
            )
            rejected_control = client.post(
                "/api/v1/projects",
                headers={
                    **auth_headers(**{"Idempotency-Key": "strict-style-" + str(value)}),
                    "Content-Type": "application/json",
                },
                content=body,
            )
            assert rejected_control.status_code == 400
        unknown = client.post(
            "/api/v1/projects",
            headers=auth_headers(**{"Idempotency-Key": "unknown-field"}),
            json={"name": "No", "private_account_id": "secret-user"},
        )
        assert unknown.status_code == 400
        assert "private_account_id" not in unknown.text
        assert "secret-user" not in unknown.text
        missing_key = client.post("/api/v1/projects", headers=auth_headers(), json={"name": "No"})
        assert missing_key.status_code == 400
        assert missing_key.json()["error"]["code"] == "validation_error"
        delete = client.delete(
            f"/api/v1/projects/{PROJECT_ID}", headers=auth_headers(**{"If-Match": '"v1"'})
        )
        assert delete.status_code == 503
        assert delete.json()["error"]["code"] == "capability_unavailable"
        assert projects.project["name"] == "Cloud draft"


def test_project_preflight_allows_only_required_cloud_headers(tmp_path):
    with client_with_projects(tmp_path)[0] as client:
        response = client.options(
            "/api/v1/projects",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,idempotency-key,if-match",
            },
        )
        assert response.status_code == 200
        allowed = response.headers["access-control-allow-headers"].lower()
        assert all(
            name in allowed
            for name in ("authorization", "content-type", "idempotency-key", "if-match")
        )
