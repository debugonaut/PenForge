"""Shared FastAPI dependency for verified identity-bound route access."""

from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.adapters.auth.base import Principal, parse_bearer_token
from app.core.errors import ApiError
from app.core.ratelimit import hash_ip, raise_for_limit

bearer = HTTPBearer(auto_error=False)


def _identity_service(request: Request):
    result = request.app.state.identity_service
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


async def identity(
    request: Request,
    response: Response,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    """Verify a single bearer token and apply Phase 01 admission controls."""
    del credentials  # HTTPBearer declares OpenAPI security; parsing is shared with S0.
    response.headers["Cache-Control"] = "no-store"
    headers = request.headers.getlist("authorization")
    if len(headers) != 1:
        raise ApiError("unauthenticated", 401)
    token = parse_bearer_token(headers[0])
    verifier = request.app.state.auth_verifier
    if verifier is None or request.app.state.settings.auth_backend != "firebase":
        raise ApiError("dependency_unavailable", 503)
    limiter = request.app.state.rate_limiter
    settings = request.app.state.settings
    if limiter is None:
        raise ApiError("dependency_unavailable", 503)
    # The peer is supplied by ASGI; forwarded headers are deliberately not trusted.
    peer = request.client.host if request.client else "127.0.0.1"
    digest = hash_ip(
        peer, request.app.state.identity_hmac_secret, key_version=settings.hmac_key_version
    )
    decision = await limiter.consume(
        "identity:ip:" + digest.hex(),
        limit=settings.identity_rate_limit,
        window_seconds=settings.rate_limit_window_seconds,
    )
    response.headers.update(decision.headers())
    raise_for_limit(decision)
    principal = await verifier.verify(token, check_revoked=True)
    if not await _identity_service(request).identity_exists(principal.firebase_uid):
        for key, limit in (
            ("identity:first:ip:" + digest.hex(), settings.identity_first_request_limit),
            ("identity:first:global", settings.identity_global_first_request_limit),
        ):
            admission = await limiter.consume(
                key, limit=limit, window_seconds=settings.rate_limit_window_seconds
            )
            raise_for_limit(admission)
    request.state.identity_ip_hash = digest
    return principal
