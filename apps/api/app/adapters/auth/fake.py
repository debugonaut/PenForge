import hmac
from uuid import UUID

from app.adapters.auth.base import Principal
from app.core.config import Settings
from app.core.errors import ApiError


class FakeAuthVerifier:
    """Fixed local principal. No users are persisted and no Firebase claims are simulated."""

    def __init__(self, settings: Settings):
        if settings.app_env not in ("local", "test"):
            raise ValueError("Fake authentication is restricted to local/test")
        self._token = settings.local_auth_token.get_secret_value()

    async def verify(self, token: str, *, check_revoked: bool = False) -> Principal:
        if not hmac.compare_digest(token.encode(), self._token.encode()):
            raise ApiError("invalid_token", 401)
        return Principal(
            user_id=UUID("00000000-0000-4000-8000-000000000001"),
            firebase_uid="local-only",
            is_anonymous=True,
            sign_in_provider="local_fake",
            email_verified=False,
        )
