"""HTTP identity boundary; transactions and ownership belong to the service."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.core.ratelimit import raise_for_limit
from app.modules.identity.schemas import ClaimRequest, ClaimResponse, MeResponse
from app.modules.identity.service import IdentityService

router = APIRouter(
    prefix="/api/v1",
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        429: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)


def service(request: Request) -> IdentityService:
    result = request.app.state.identity_service
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


@router.get("/me", response_model=MeResponse, operation_id="getMe")
async def me(
    request: Request,
    principal: Annotated[Principal, Depends(identity)],
) -> MeResponse:
    result = await service(request).me(principal, first_seen_ip_hash=request.state.identity_ip_hash)
    return MeResponse.model_validate(result)


@router.post("/identity/claim", response_model=ClaimResponse, operation_id="claimIdentity")
async def claim(
    request: Request,
    response: Response,
    body: ClaimRequest,
    principal: Annotated[Principal, Depends(identity)],
) -> ClaimResponse:
    settings = request.app.state.settings
    limiter = request.app.state.rate_limiter
    decision = await limiter.consume(
        "identity:claim:" + principal.firebase_uid,
        limit=settings.claim_rate_limit,
        window_seconds=settings.rate_limit_window_seconds,
    )
    response.headers.update(decision.headers())
    raise_for_limit(decision)
    guest = await request.app.state.auth_verifier.verify(
        body.guest_id_token.get_secret_value(), check_revoked=True
    )
    result = await service(request).claim(principal, guest, request_id=request.state.request_id)
    return ClaimResponse.model_validate(result)
