"""Policy and HTTP admission tests with synthetic identities; no external accounts."""

from dataclasses import replace

import pytest
from app.adapters.auth.base import Principal
from app.core.config import Settings
from app.core.errors import ApiError
from app.main import create_app
from app.modules.accountability.policy import current_policy
from app.modules.accountability.schemas import AgreementCreate
from fastapi.testclient import TestClient
from pydantic import ValidationError

REGISTERED = Principal(None, "synthetic-owner", False, "password", True)


class Verifier:
    def __init__(self, principal):
        self.principal = principal

    async def verify(self, token, *, check_revoked=False):
        assert check_revoked
        return self.principal


class Identity:
    async def identity_exists(self, uid):
        return True


class Admission:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def access(self, principal, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise ApiError(self.error, 403)


class Projects:
    async def create(self, *args, **kwargs):
        raise AssertionError("Refused requests must never mutate projects")


@pytest.mark.parametrize("field", ["terms_accepted", "handwriting_rights_confirmed"])
@pytest.mark.parametrize("value", [False, 0, 1, "true", "false", None])
def test_receipts_require_two_real_affirmative_booleans(field, value):
    body = dict(policy_sha256="a" * 64, terms_accepted=True, handwriting_rights_confirmed=True)
    body[field] = value
    with pytest.raises(ValidationError):
        AgreementCreate.model_validate(body)


def test_policy_hash_binds_full_text_and_operator_and_is_stable():
    local = Settings(app_env="test", hmac_secret="s" * 32)
    first = current_policy(local)
    assert first == current_policy(local)
    assert len(first["sha256"]) == 64 and first["ready"]
    changed = current_policy(
        Settings(app_env="test", hmac_secret="s" * 32, service_operator_name="Test operator")
    )
    assert changed["sha256"] != first["sha256"]
    assert first["document"]["operator"]["name"] is None
    assert not Settings(app_env="production", storage_backend="disabled").service_ready
    # Even explicit operator settings cannot release a draft policy.
    hosted = Settings(
        app_env="production",
        storage_backend="disabled",
        service_operator_name="Synthetic operator",
        service_operator_contact="operator@example.invalid",
        service_terms_reviewed=True,
    )
    assert not current_policy(hosted)["ready"]


@pytest.mark.parametrize(
    "principal, error, service_error",
    [
        (replace(REGISTERED, is_anonymous=True), "account_required", None),
        (replace(REGISTERED, email_verified=False), "email_verification_required", None),
        (REGISTERED, "terms_required", "terms_required"),
    ],
)
def test_refused_creation_never_reaches_domain_service(tmp_path, principal, error, service_error):
    guard = Admission(service_error)
    app = create_app(
        Settings(
            app_env="test",
            auth_backend="firebase",
            firebase_project_id="penforge-test",
            storage_root=tmp_path,
        ),
        auth_verifier=Verifier(principal),
        identity_service=Identity(),
        projects_service=Projects(),
        accountability_service=guard,
    )
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/v1/projects",
            headers={"Authorization": "Bearer test", "Idempotency-Key": "test-key"},
            json={"name": "Private draft", "script_code": "Latn"},
        )
        assert response.status_code == 403 and response.json()["error"]["code"] == error
        assert len(guard.calls) == (1 if service_error else 0)


def test_missing_accountability_service_fails_closed(tmp_path):
    app = create_app(
        Settings(
            app_env="test",
            auth_backend="firebase",
            firebase_project_id="penforge-test",
            storage_root=tmp_path,
        ),
        auth_verifier=Verifier(REGISTERED),
        identity_service=Identity(),
        projects_service=Projects(),
    )
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/v1/projects",
            headers={"Authorization": "Bearer test", "Idempotency-Key": "key"},
            json={"name": "Private draft", "script_code": "Latn"},
        )
        assert response.status_code == 503


def test_existing_privacy_reference_bytes_remain_compatible():
    from app.core.privacy_reference import privacy_reference

    assert (
        privacy_reference(b"privacy-fixture-key-v1" * 2, "subject", "synthetic-uid").hex()
        == "5e885ffd2d06ee1e57da4e9140ff92ec91b5426f7bafaece1776f9f6d4a55c76"
    )
