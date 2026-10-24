"""Offline verifier evidence. Hosted Firebase/Google certificate retrieval is a separate gate."""

import asyncio
import time
from types import SimpleNamespace

import jwt
import pytest
from app.adapters.auth.base import parse_bearer_token
from app.adapters.auth.firebase import FirebaseAuthVerifier
from app.core.config import Settings
from app.core.errors import ApiError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from firebase_admin import auth
from google.auth.credentials import AnonymousCredentials


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def verified_claims():
    now = int(time.time())
    return {
        "uid": "firebase-user",
        "sub": "firebase-user",
        "aud": "penforge-unit-test",
        "iss": "https://securetoken.google.com/penforge-unit-test",
        "auth_time": now - 60,
        "iat": now - 10,
        "exp": now + 3600,
        "firebase": {"sign_in_provider": "anonymous"},
    }


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    instance = FirebaseAuthVerifier(
        Settings(app_env="test", auth_backend="firebase", firebase_project_id="penforge-unit-test")
    )
    yield instance
    instance.close()


def test_verified_claims_only_and_revocation_is_mandatory(verifier, monkeypatch, verified_claims):
    calls = []

    def verify(token, *, app, check_revoked):
        calls.append((token, app, check_revoked))
        return verified_claims

    monkeypatch.setattr(auth, "verify_id_token", verify)
    principal = run(verifier.verify("signed-token", check_revoked=False))
    assert principal.firebase_uid == "firebase-user"
    assert principal.user_id is None
    assert principal.is_anonymous
    assert not principal.email_verified
    assert principal.auth_time == verified_claims["auth_time"]
    assert calls[0][2] is True
    verified_claims["firebase"]["sign_in_provider"] = "google.com"
    verified_claims["email_verified"] = True
    assert not run(verifier.verify("signed-token")).is_anonymous
    assert run(verifier.verify("signed-token")).email_verified


@pytest.mark.parametrize(
    "field,value",
    [
        ("aud", "wrong-project"),
        ("iss", "https://securetoken.google.com/wrong-project"),
        ("sub", "different-subject"),
        ("uid", ""),
        ("uid", "x" * 129),
        ("auth_time", 0),
        ("auth_time", True),
        ("auth_time", "123"),
        ("auth_time", 9999999999),
        ("iat", 9999999999),
        ("exp", 1),
        ("firebase", {}),
        ("firebase", {"sign_in_provider": "anonymous", "tenant": "unconfigured"}),
        ("firebase", {"sign_in_provider": "bad\nprovider"}),
        ("email_verified", "true"),
    ],
)
def test_invalid_verified_claim_shape_fails_closed(
    verifier, monkeypatch, verified_claims, field, value
):
    verified_claims[field] = value
    monkeypatch.setattr(auth, "verify_id_token", lambda *a, **k: verified_claims)
    with pytest.raises(ApiError) as error:
        run(verifier.verify("token"))
    assert (error.value.code, error.value.status) == ("invalid_token", 401)


@pytest.mark.parametrize(
    "failure",
    [
        auth.InvalidIdTokenError("PRIVATE_TOKEN"),
        auth.ExpiredIdTokenError("PRIVATE_TOKEN", None),
        auth.RevokedIdTokenError("PRIVATE_TOKEN"),
        auth.UserDisabledError("PRIVATE_UID"),
        auth.UserNotFoundError("PRIVATE_UID"),
    ],
)
def test_sdk_identity_failures_are_typed_and_sanitized(verifier, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(auth, "verify_id_token", fail)
    with pytest.raises(ApiError) as error:
        run(verifier.verify("PRIVATE_TOKEN"))
    assert (error.value.code, error.value.status) == ("invalid_token", 401)
    assert "PRIVATE" not in str(error.value)
    assert error.value.__suppress_context__


def test_sdk_outage_is_not_invalid_identity(verifier, monkeypatch):
    def fail(*args, **kwargs):
        raise auth.CertificateFetchError("PRIVATE_URL", None)

    monkeypatch.setattr(auth, "verify_id_token", fail)
    with pytest.raises(ApiError) as error:
        run(verifier.verify("PRIVATE_TOKEN"))
    assert (error.value.code, error.value.status) == ("dependency_unavailable", 503)
    assert "PRIVATE" not in str(error.value)


def test_ambient_emulator_cannot_be_enabled_after_start(verifier, monkeypatch, verified_claims):
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
    monkeypatch.setattr(auth, "verify_id_token", lambda *a, **k: pytest.fail("SDK must not run"))
    with pytest.raises(ApiError) as error:
        run(verifier.verify("unsigned-token"))
    assert error.value.status == 503


def test_emulator_requires_matching_explicit_process_environment(monkeypatch):
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    settings = Settings(
        app_env="test",
        auth_backend="firebase",
        firebase_project_id="demo-penforge",
        firebase_allow_emulator=True,
        firebase_auth_emulator_host="127.0.0.1:9099",
    )
    with pytest.raises(ValueError):
        FirebaseAuthVerifier(settings)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "Basic abc",
        "Bearer",
        "Bearer ",
        "Bearer a b",
        "Bearer a,b",
        "Bearer a\n",
        "Bearer " + "a" * 16385,
    ],
)
def test_bearer_header_is_one_bounded_token(value):
    with pytest.raises(ApiError) as error:
        parse_bearer_token(value)
    assert error.value.code == "unauthenticated"


def test_bearer_scheme_is_case_insensitive():
    assert parse_bearer_token("bearer abc.def.signature") == "abc.def.signature"


@pytest.fixture
def actual_sdk_offline(verifier, monkeypatch):
    """Real SDK + real RSA signature; certificate source and user status are local fixtures."""
    import google.oauth2.id_token
    from firebase_admin import _auth_client, credentials

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setattr(
        credentials.ApplicationDefault, "get_credential", lambda self: AnonymousCredentials()
    )
    monkeypatch.setattr(
        google.oauth2.id_token, "_fetch_certs", lambda *a, **k: {"local-rsa": public}
    )
    state = SimpleNamespace(disabled=False, tokens_valid_after_timestamp=0)
    monkeypatch.setattr(_auth_client.Client, "get_user", lambda self, uid: state)
    return verifier, key, state


def signed_token(key, claims):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "local-rsa"})


def test_real_sdk_accepts_signature_and_checks_revocation(actual_sdk_offline, verified_claims):
    verifier, key, state = actual_sdk_offline
    token = signed_token(key, verified_claims)
    assert run(verifier.verify(token)).firebase_uid == "firebase-user"
    state.tokens_valid_after_timestamp = (verified_claims["iat"] + 1) * 1000
    with pytest.raises(ApiError) as error:
        run(verifier.verify(token))
    assert error.value.code == "invalid_token"
    state.tokens_valid_after_timestamp = 0
    state.disabled = True
    with pytest.raises(ApiError) as error:
        run(verifier.verify(token))
    assert error.value.code == "invalid_token"


@pytest.mark.parametrize(
    "mutation", ["signature", "aud", "iss", "exp", "iat", "algorithm", "custom"]
)
def test_real_sdk_rejects_forged_expired_wrong_project_and_custom_tokens(
    actual_sdk_offline, verified_claims, mutation
):
    verifier, key, state = actual_sdk_offline
    if mutation == "aud":
        verified_claims["aud"] = "different-project"
    elif mutation == "iss":
        verified_claims["iss"] = "https://attacker.example"
    elif mutation == "exp":
        verified_claims["exp"] = int(time.time()) - 1
    elif mutation == "iat":
        verified_claims["iat"] = int(time.time()) + 60
    elif mutation == "custom":
        verified_claims["aud"] = (
            "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit"
        )
    token = signed_token(key, verified_claims)
    if mutation == "signature":
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = signed_token(other_key, verified_claims)
    elif mutation == "algorithm":
        token = jwt.encode(
            verified_claims, "test-key" * 4, algorithm="HS256", headers={"kid": "local-rsa"}
        )
    with pytest.raises(ApiError) as error:
        run(verifier.verify(token))
    assert (error.value.code, error.value.status) == ("invalid_token", 401)
