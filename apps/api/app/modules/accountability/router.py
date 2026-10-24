from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.core.ratelimit import hash_ip, raise_for_limit
from app.modules.accountability.policy import current_policy
from app.modules.accountability.schemas import (
    AbuseReceipt,
    AbuseReportCreate,
    AccessResponse,
    AgreementCreate,
    PolicyResponse,
)
from app.modules.accountability.service import service

router = APIRouter(
    prefix="/api/v1",
    tags=["service-access"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 409, 422, 429, 503)},
)


@router.get("/service-policy", response_model=PolicyResponse, operation_id="getServicePolicy")
async def policy(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return current_policy(request.app.state.settings)


@router.get("/me/service-access", response_model=AccessResponse, operation_id="getServiceAccess")
async def access(request: Request, principal: Annotated[Principal, Depends(identity)]) -> dict:
    return await service(request).access(principal)


@router.post(
    "/me/service-agreements", response_model=AccessResponse, operation_id="acceptServicePolicy"
)
async def accept(
    body: AgreementCreate, request: Request, principal: Annotated[Principal, Depends(identity)]
) -> dict:
    return await service(request).access(principal, body)


@router.post(
    "/abuse-reports", response_model=AbuseReceipt, status_code=201, operation_id="reportAbuse"
)
async def report(body: AbuseReportCreate, request: Request, response: Response) -> AbuseReceipt:
    response.headers["Cache-Control"] = "no-store"
    limiter = request.app.state.rate_limiter
    if limiter is None:
        raise ApiError("dependency_unavailable", 503)
    settings = request.app.state.settings
    peer = request.client.host if request.client else "127.0.0.1"
    digest = hash_ip(
        peer, request.app.state.identity_hmac_secret, key_version=settings.hmac_key_version
    )
    decision = await limiter.consume("abuse:ip:" + digest.hex(), limit=5, window_seconds=3600)
    raise_for_limit(decision)
    # Global bound also caps distributed submissions until hosted controls exist.
    raise_for_limit(await limiter.consume("abuse:global", limit=100, window_seconds=3600))
    return AbuseReceipt(report_id=await service(request).report(body))
