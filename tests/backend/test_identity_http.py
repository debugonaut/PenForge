"""HTTP policy tests use injected test doubles; no fake identity is enabled in production."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.adapters.auth.base import Principal
from app.core.config import Settings
from app.core.errors import ApiError
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError

USER_ID = UUID("22222222-2222-4222-8222-222222222222")
SOURCE_ID = UUID("11111111-1111-4111-8111-111111111111")


class Verifier:
    def __init__(self):
        self.calls = []

    async def verify(self, token, *, check_revoked=False):
        self.calls.append((token, check_revoked))
        if token not in {"account-token", "guest-token"}:
            raise ApiError("invalid_token", 401)
        anonymous = token == "guest-token"
        return Principal(
            user_id=None,
            firebase_uid="guest-uid" if anonymous else "account-uid",
            is_anonymous=anonymous,
            sign_in_provider="anonymous" if anonymous else "password",
            email_verified=False,
            auth_time=100,
            token_issued_at=100,
        )


class Identity:
    def __init__(self):
        self.calls = []
        self.failure = None

    async def identity_exists(self, uid):
        return True

    async def me(self, principal, first_seen_ip_hash=None):
        self.calls.append((principal, first_seen_ip_hash))
        if self.failure:
            raise self.failure
        return {
            "user_id": USER_ID,
            "firebase_uid": principal.firebase_uid,
            "is_anonymous": principal.is_anonymous,
            "sign_in_provider": principal.sign_in_provider,
            "email_verified": False,
            "tier": "free_guest" if principal.is_anonymous else "free_user",
            "created_at": datetime(2026, 10, 19, tzinfo=UTC),
            "guest_creations": {
                "limit": 3,
                "used": 0,
                "remaining": 3,
                "applicable": principal.is_anonymous,
            },
            "quotas": {"configured": False, "over_limit": False, "balances": []},
        }

    async def claim(self, destination, guest, request_id=None):
        self.calls.append((destination, guest, request_id))
        return {
            "mode": "transferred",
            "source_user_id": SOURCE_ID,
            "destination_user_id": USER_ID,
            "transferred": {"projects": 0, "assets": 0, "jobs": 0, "reservations": 0},
            "guest_creations_preserved": 0,
            "over_limit": False,
        }


def app_with_identity(tmp_path, **settings):
    verifier = Verifier()
    identity = Identity()
    config = Settings(
        app_env="test",
        storage_root=tmp_path / "objects",
        auth_backend="firebase",
        firebase_project_id="penforge-test",
        **settings,
    )
    return create_app(config, auth_verifier=verifier, identity_service=identity), verifier, identity


def client_for(app):
    return TestClient(app, client=("127.0.0.1", 50000))


def test_identity_is_explicitly_unavailable_without_firebase_configuration(tmp_path):
    config = Settings(app_env="test", storage_root=tmp_path / "objects")
    with client_for(create_app(config)) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert client.get("/api/v1/me").status_code == 401
        response = client.get("/api/v1/me", headers={"authorization": "Bearer account-token"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "dependency_unavailable"


@pytest.mark.parametrize(
    "authorization", ["Basic abc", "Bearer", "Bearer ", "Bearer a b", "Bearer a,b"]
)
def test_identity_http_rejects_malformed_bearer_before_verification(tmp_path, authorization):
    app, verifier, identity = app_with_identity(tmp_path)
    with client_for(app) as client:
        response = client.get("/api/v1/me", headers={"authorization": authorization})
        assert response.status_code == 401
    assert not verifier.calls
    assert not identity.calls


def test_duplicate_authorization_and_cookie_tokens_are_rejected(tmp_path):
    app, verifier, identity = app_with_identity(tmp_path)
    with client_for(app) as client:
        response = client.get(
            "/api/v1/me",
            headers=[
                ("authorization", "Bearer account-token"),
                ("authorization", "Bearer guest-token"),
            ],
        )
        assert response.status_code == 401
        response = client.get(
            "/api/v1/me",
            params={"token": "account-token"},
            headers={"cookie": "authorization=account-token"},
        )
        assert response.status_code == 401
    assert not verifier.calls


def test_identity_uses_verified_claims_hmac_peer_and_truthful_unconfigured_quotas(tmp_path):
    app, verifier, identity = app_with_identity(tmp_path)
    with client_for(app) as client:
        response = client.get(
            "/api/v1/me",
            headers={"authorization": "Bearer guest-token", "x-forwarded-for": "198.51.100.5"},
        )
        assert response.status_code == 200
        assert response.json()["is_anonymous"]
        assert response.json()["quotas"] == {
            "configured": False,
            "over_limit": False,
            "balances": [],
        }
        assert response.json()["guest_creations"]["remaining"] == 3
        assert "RateLimit-Limit" in response.headers
    assert verifier.calls == [("guest-token", True)]
    assert isinstance(identity.calls[0][1], bytes)
    assert len(identity.calls[0][1]) == 32
    assert b"127.0.0.1" not in identity.calls[0][1]


def test_rate_limit_http_returns_typed_429_and_retry_headers(tmp_path):
    app, verifier, identity = app_with_identity(tmp_path, identity_rate_limit=1)
    with client_for(app) as client:
        assert (
            client.get("/api/v1/me", headers={"authorization": "Bearer account-token"}).status_code
            == 200
        )
        response = client.get("/api/v1/me", headers={"authorization": "Bearer account-token"})
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"
        assert response.json()["error"]["retryable"]
        assert response.headers["ratelimit-remaining"] == "0"
        assert int(response.headers["retry-after"]) >= 1
    assert len(verifier.calls) == 1


def test_claim_verifies_both_tokens_with_revocation_and_returns_counts_only(tmp_path):
    app, verifier, identity = app_with_identity(tmp_path)
    with client_for(app) as client:
        response = client.post(
            "/api/v1/identity/claim",
            headers={"authorization": "Bearer account-token"},
            json={"guest_id_token": "guest-token"},
        )
        assert response.status_code == 200
        assert response.json()["mode"] == "transferred"
        assert "token" not in response.text
        assert "storage_key" not in response.text
    assert verifier.calls == [("account-token", True), ("guest-token", True)]
    assert identity.calls[0][0].firebase_uid == "account-uid"
    assert identity.calls[0][1].firebase_uid == "guest-uid"


def test_claim_validation_never_echoes_credentials_or_accepts_owner_assertions(tmp_path):
    app, verifier, identity = app_with_identity(tmp_path)
    with client_for(app) as client:
        response = client.post(
            "/api/v1/identity/claim",
            headers={"authorization": "Bearer account-token"},
            json={
                "guest_id_token": {"password": "PRIVATE_PASSWORD"},
                "owner_user_id": str(USER_ID),
            },
        )
        assert response.status_code == 400
        assert "PRIVATE" not in response.text
        assert "guest_id_token" not in response.text
        assert str(USER_ID) not in response.text
    assert not identity.calls


@pytest.mark.parametrize("code", ["account_suspended", "account_deleting", "account_merged"])
def test_inactive_identity_errors_remain_typed(tmp_path, code):
    app, verifier, identity = app_with_identity(tmp_path)
    identity.failure = ApiError(code, 403)
    with client_for(app) as client:
        response = client.get("/api/v1/me", headers={"authorization": "Bearer account-token"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("app_env", ["preview", "production"])
def test_emulator_configuration_is_impossible_in_nonlocal_environment(app_env, monkeypatch):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
    with pytest.raises(ValidationError):
        Settings(
            app_env=app_env,
            auth_backend="disabled",
            storage_backend="disabled",
            rate_limit_backend="disabled",
            firebase_allow_emulator=True,
            firebase_auth_emulator_host="127.0.0.1:9099",
            firebase_project_id="demo-penforge",
        )


def test_ambient_emulator_requires_explicit_allow_and_demo_project(monkeypatch):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
    with pytest.raises(ValidationError):
        Settings(app_env="test", firebase_project_id="demo-penforge")
    with pytest.raises(ValidationError):
        Settings(app_env="test", firebase_project_id="real-penforge", firebase_allow_emulator=True)
