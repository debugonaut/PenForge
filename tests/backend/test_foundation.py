import asyncio
import json
import logging
from uuid import UUID, uuid4

import pytest
from app.adapters.auth.fake import FakeAuthVerifier
from app.core.config import Settings
from app.core.logging import SafeJsonFormatter
from app.core.security import get_principal
from app.internal import create_internal_app
from app.main import create_app
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError


def config(tmp_path, **overrides):
    return Settings(
        app_env="test", storage_root=tmp_path / "objects", auth_backend="fake", **overrides
    )


def test_health_is_liveness_only_and_readiness_not_public(tmp_path):
    with TestClient(create_app(config(tmp_path))) as client:
        response = client.get("/api/v1/health")
        assert response.json() == {"status": "ok"}
        UUID(response.headers["x-request-id"])
        assert client.get("/ready").status_code == 404
        assert client.get("/api/v1/projects").status_code == 401
        assert set(client.get("/openapi.json").json()["paths"]) == {
            "/api/v1/projects/{project_id}/capabilities",
            "/api/v1/projects/{project_id}/style-profile",
            "/api/v1/projects/{project_id}/glyphs/{glyph_id}/alternates",
            "/api/v1/projects/{project_id}/glyphs/{glyph_id}/alternate-enabled",
            "/api/v1/exports/{export_id}",
            "/api/v1/exports/{export_id}/download-url",
            "/api/v1/projects/{project_id}/exports",
            "/api/v1/health",
            "/api/v1/contracts",
            "/api/v1/consent",
            "/api/v1/privacy/contributions",
            "/api/v1/privacy/deletion-requests",
            "/api/v1/privacy/deletion-requests/{request_id}",
            "/api/v1/me",
            "/api/v1/service-policy",
            "/api/v1/me/service-access",
            "/api/v1/me/service-agreements",
            "/api/v1/abuse-reports",
            "/api/v1/identity/claim",
            "/api/v1/projects",
            "/api/v1/projects/{project_id}",
            "/api/v1/projects/{project_id}/workspace",
            "/api/v1/documents",
            "/api/v1/documents/{document_id}",
            "/api/v1/projects/{project_id}/uploads",
            "/api/v1/uploads/{asset_id}/upload-url",
            "/api/v1/uploads/{asset_id}/complete",
            "/api/v1/assets/{asset_id}",
            "/api/v1/assets/{asset_id}/download-url",
            "/api/v1/projects/{project_id}/samples",
            "/api/v1/jobs/{job_id}",
            "/api/v1/jobs/{job_id}/cancel",
            "/api/v1/jobs/{job_id}/events",
            "/api/v1/projects/{project_id}/jobs",
            "/api/v1/projects/{project_id}/analysis-jobs/{job_id}",
            "/api/v1/templates",
            "/api/v1/templates/{template_id}/{version}/{page_index}.{format}",
            "/api/v1/calibration-definitions/{script_code}",
            "/api/v1/projects/{project_id}/glyphs",
            "/api/v1/projects/{project_id}/glyphs/space",
            "/api/v1/projects/{project_id}/glyphs/{glyph_id}",
            "/api/v1/projects/{project_id}/glyphs/{glyph_id}/candidates",
            "/api/v1/projects/{project_id}/glyphs/{glyph_id}/select-candidate",
            "/api/v1/projects/{project_id}/snapshots",
            "/api/v1/projects/{project_id}/snapshots/{snapshot_id}",
            "/api/v1/projects/{project_id}/coverage",
            "/api/v1/projects/{project_id}/capture-prompts",
            "/api/v1/projects/{project_id}/calibration/next",
        }


@pytest.mark.parametrize("bad_id", ["line\nprivate", "secret-token", "x" * 10000, "non-ascii-\xff"])
def test_malformed_request_ids_are_replaced(tmp_path, bad_id):
    with TestClient(create_app(config(tmp_path))) as client:
        response = client.get("/missing", headers={b"x-request-id": bad_id.encode("latin1")})
        actual = response.headers["x-request-id"]
        assert actual != bad_id
        UUID(actual)
        assert response.json()["error"]["request_id"] == actual


def test_uuid_request_id_and_cors(tmp_path):
    request_id = str(uuid4())
    with TestClient(create_app(config(tmp_path))) as client:
        response = client.get(
            "/api/v1/health",
            headers={
                "x-request-id": request_id,
                "origin": "http://localhost:3000",
            },
        )
        assert response.headers["x-request-id"] == request_id
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert (
            "access-control-allow-origin"
            not in client.get(
                "/api/v1/health", headers={"origin": "https://untrusted.example"}
            ).headers
        )


def test_validation_exception_and_oversized_input_never_echo_private_values(tmp_path):
    app = create_app(config(tmp_path, request_max_bytes=1024))

    class Body(BaseModel):
        count: int

    @app.post("/test-input")
    async def test_input(body: Body):
        raise RuntimeError("PRIVATE_EXCEPTION")

    with TestClient(app) as client:
        response = client.post("/test-input", json={"count": {"token": "PRIVATE_INPUT"}})
        assert response.status_code == 400
        assert response.json()["error"]["details"] == {"fields": ["/body/count"]}
        response = client.post("/test-input", json={"count": 1})
        assert response.status_code == 500
        assert "PRIVATE_EXCEPTION" not in response.text
        response = client.post("/test-input", content=b"x" * 2048)
        assert response.status_code == 413


def test_logs_drop_nested_secrets_content_urls_and_exception_strings():
    record = logging.LogRecord("test", logging.ERROR, "", 0, "PRIVATE_MESSAGE", (), None)
    record.fields = {
        "request_id": str(uuid4()),
        "status_code": 400,
        "Authorization": "PRIVATE_AUTH",
        "outline": {"x": "PRIVATE_OUTLINE"},
        "metadata": {"token": "PRIVATE_NESTED"},
        "text": "PRIVATE_TEXT",
        "url": "https://private.example/?token=PRIVATE_URL",
        "image": "PRIVATE_IMAGE",
        "duration_ms": float("nan"),
        "user_id": "PRIVATE_ID",
    }
    output = SafeJsonFormatter().format(record)
    assert "PRIVATE" not in output
    assert json.loads(output)["status_code"] == 400
    assert json.loads(output)["event"] == "redacted"


def test_chunked_body_limit_is_enforced_before_parsing(tmp_path):
    import httpx

    async def run():
        async def chunks():
            yield b"x" * 600
            yield b"x" * 600

        app = create_app(config(tmp_path, request_max_bytes=1024))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/missing", content=chunks())
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "payload_too_large"

    asyncio.run(run())


@pytest.mark.parametrize("environment", ["preview", "production"])
def test_fake_auth_and_storage_refuse_nonlocal_environments(environment):
    with pytest.raises(ValidationError):
        Settings(app_env=environment)
    settings = Settings(app_env=environment, auth_backend="disabled", storage_backend="disabled")
    with pytest.raises(ValueError):
        FakeAuthVerifier(settings)


def test_fake_verifier_is_injected_and_missing_wrong_tokens_rejected(tmp_path):
    settings = config(tmp_path)
    app = create_app(settings)

    @app.get("/test-auth")
    async def authenticated(principal=Depends(get_principal)):
        return {"is_anonymous": principal.is_anonymous}

    with TestClient(app) as client:
        assert client.get("/test-auth").status_code == 401
        assert (
            client.get("/test-auth", headers={"authorization": "Bearer wrong"}).status_code == 401
        )
        assert client.get(
            "/test-auth",
            headers={"authorization": "Bearer " + settings.local_auth_token.get_secret_value()},
        ).json() == {"is_anonymous": True}


def test_internal_readiness_requires_separate_app_and_secret(tmp_path):
    with TestClient(create_internal_app(config(tmp_path))) as client:
        assert client.get("/ready").status_code == 404
    with TestClient(
        create_internal_app(
            config(
                tmp_path,
                readiness_token="x" * 32,
                database_url="postgresql+psycopg://missing:missing@127.0.0.1:1/missing?connect_timeout=1",
            )
        )
    ) as client:
        response = client.get("/ready", headers={"authorization": "Bearer " + "x" * 32})
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}


@pytest.mark.parametrize("origin", ["*", "https://a.example/path", "https://x:y@a.example"])
def test_invalid_cors_config_fails(origin):
    with pytest.raises(ValidationError):
        Settings(cors_allowed_origins=[origin])


def test_invalid_database_url_is_rejected_without_showing_secret():
    with pytest.raises(ValidationError) as error:
        Settings(database_url="postgresql+psycopg://user:PRIVATE_SECRET@host:invalid/db")
    assert "PRIVATE_SECRET" not in str(error.value)


def test_nested_validation_pointers_only_reflect_safe_schema_fields(tmp_path):
    from pydantic import ConfigDict

    class Item(BaseModel):
        model_config = ConfigDict(extra="forbid")
        count: int
        token: int

    class Body(BaseModel):
        items: list[Item]

    app = create_app(config(tmp_path))

    @app.post("/test-nested")
    async def nested(body: Body):
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.post(
            "/test-nested",
            json={
                "items": [
                    {"count": "PRIVATE", "token": "PRIVATE_TOKEN", "PRIVATE_KEY": "PRIVATE_VALUE"}
                ]
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["details"]["fields"] == [
            "/body/items/0",
            "/body/items/0/count",
        ]
        assert "PRIVATE" not in response.text
        assert "token" not in response.text
