import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError


@dataclass(frozen=True)
class Principal:
    # Firebase proves a UID; only the identity service can assign an internal user ID.
    user_id: UUID | None
    firebase_uid: str
    is_anonymous: bool
    sign_in_provider: str
    email_verified: bool
    auth_time: int = 0
    token_issued_at: int = 0


def parse_bearer_token(authorization: str) -> str:
    """Accept one RFC 6750 token, never cookies, query parameters or owner assertions."""
    if not authorization or len(authorization) > 16391:
        raise ApiError("unauthenticated", 401)
    match = re.fullmatch(r"Bearer ([A-Za-z0-9\-._~+/]+=*)", authorization, re.IGNORECASE)
    if match is None:
        raise ApiError("unauthenticated", 401)
    return match.group(1)


class AuthVerifier(Protocol):
    async def verify(self, token: str, *, check_revoked: bool = False) -> Principal: ...
