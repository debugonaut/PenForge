from fastapi import Request

from app.adapters.auth.base import Principal, parse_bearer_token
from app.core.errors import ApiError


async def get_principal(request: Request) -> Principal:
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise ApiError("unauthenticated", 401)
    token = parse_bearer_token(values[0])
    verifier = request.app.state.auth_verifier
    if verifier is None:
        raise ApiError("dependency_unavailable", 503)
    return await verifier.verify(token, check_revoked=True)
