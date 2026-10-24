"""Firebase Admin ID-token verification; no unverified claim becomes an identity."""

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

import firebase_admin  # type: ignore[import-untyped]
from firebase_admin import auth

from app.adapters.auth.base import Principal
from app.core.config import Settings
from app.core.errors import ApiError


class FirebaseAuthVerifier:
    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.time):
        if not settings.firebase_project_id:
            raise ValueError("Firebase authentication requires an explicit project ID")
        self._project_id = settings.firebase_project_id
        self._emulator_host = settings.firebase_auth_emulator_host
        self._allow_emulator = settings.firebase_allow_emulator
        self._environment = settings.app_env
        self._clock = clock
        self._guard_emulator()
        # ADC is resolved lazily by the SDK. Import/OpenAPI generation never contacts Firebase.
        self._app = firebase_admin.initialize_app(
            options={"projectId": self._project_id, "httpTimeout": 10},
            name="penforge-" + uuid4().hex,
        )

    def _guard_emulator(self) -> None:
        ambient = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")
        if self._allow_emulator:
            if (
                self._environment not in ("local", "test")
                or not self._project_id.startswith("demo-")
                or not self._emulator_host
                or ambient != self._emulator_host
            ):
                raise ValueError("Auth emulator requires explicit matching local configuration")
        elif ambient or self._emulator_host:
            # Admin SDK interprets this ambient variable as permission to skip signatures.
            raise ValueError("Unsigned emulator authentication is not authorized")

    async def verify(self, token: str, *, check_revoked: bool = False) -> Principal:
        if not isinstance(token, str) or not token or len(token) > 16384 or token.strip() != token:
            raise ApiError("invalid_token", 401)
        try:
            self._guard_emulator()
        except ValueError:
            raise ApiError("dependency_unavailable", 503) from None
        return await asyncio.to_thread(self._verify, token)

    def _verify(self, token: str) -> Principal:
        try:
            self._guard_emulator()
        except ValueError:
            raise ApiError("dependency_unavailable", 503) from None
        try:
            # Always check revocation/disabled status, including ordinary identity reads.
            # check_revoked stays in the protocol for other adapters and future route policy.
            claims = auth.verify_id_token(token, app=self._app, check_revoked=True)
        except (
            auth.InvalidIdTokenError,
            auth.UserDisabledError,
            auth.UserNotFoundError,
            ValueError,
            TypeError,
        ):
            raise ApiError("invalid_token", 401) from None
        except Exception:
            # Credentials, transport and certificate-fetch failures are unavailable, never
            # trusted identities. SDK exception text can contain tokens/URLs; discard it.
            raise ApiError("dependency_unavailable", 503) from None
        return self._principal(claims)

    async def delete_identity(self, uid: str) -> None:
        """Idempotent admin erasure using the same explicitly configured Firebase app."""
        from app.adapters.storage.base import StorageError

        self._guard_emulator()
        try:
            await asyncio.to_thread(auth.delete_user, uid, app=self._app)
        except auth.UserNotFoundError:
            pass
        except Exception:
            raise StorageError("identity_deletion_unavailable") from None

    def _principal(self, claims: dict[str, Any]) -> Principal:
        now = self._clock()
        uid = claims.get("uid")
        firebase = claims.get("firebase")
        timestamps = [claims.get(field) for field in ("auth_time", "iat", "exp")]
        if (
            not isinstance(uid, str)
            or not 1 <= len(uid) <= 128
            or claims.get("sub") != uid
            or claims.get("aud") != self._project_id
            or claims.get("iss") != "https://securetoken.google.com/" + self._project_id
            or not isinstance(firebase, dict)
            or firebase.get("tenant") is not None
            or any(type(value) is not int or value <= 0 for value in timestamps)
        ):
            raise ApiError("invalid_token", 401)
        authenticated, issued, expires = (cast(int, value) for value in timestamps)
        # The Auth emulator deliberately skips signature and time verification. Enforce
        # temporal validity ourselves in both modes, so expiry tests remain meaningful.
        if not authenticated <= issued <= now < expires:
            raise ApiError("invalid_token", 401)
        provider = firebase.get("sign_in_provider")
        if (
            not isinstance(provider, str)
            or not 1 <= len(provider) <= 128
            or any(ord(char) < 33 or ord(char) > 126 for char in provider)
            or type(claims.get("email_verified", False)) is not bool
        ):
            raise ApiError("invalid_token", 401)
        return Principal(
            user_id=None,
            firebase_uid=uid,
            is_anonymous=provider == "anonymous",
            sign_in_provider=provider,
            email_verified=claims.get("email_verified", False),
            auth_time=authenticated,
            token_issued_at=issued,
        )

    def close(self) -> None:
        firebase_admin.delete_app(self._app)
