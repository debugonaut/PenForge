"""Private controls use verified bearer auth; an owner-issued receipt survives erasure."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import bearer, identity
from app.core.errors import ApiError, ErrorEnvelope
from app.modules.privacy.schemas import (
    ConsentPut,
    ConsentView,
    ContributionCreate,
    ContributionView,
    DeletionCreate,
    DeletionView,
)

receipt_header = APIKeyHeader(
    name="X-Deletion-Receipt", scheme_name="DeletionReceipt", auto_error=False
)

router = APIRouter(
    prefix="/api/v1",
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)


def service(request):
    result = getattr(request.app.state, "privacy_service", None)
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


@router.get("/consent", response_model=ConsentView, operation_id="getConsent")
async def get_consent(request: Request, principal: Annotated[Principal, Depends(identity)]):
    return await service(request).consent(principal)


@router.put("/consent", response_model=ConsentView, operation_id="putConsent")
async def put_consent(
    request: Request, body: ConsentPut, principal: Annotated[Principal, Depends(identity)]
):
    return await service(request).put_consent(principal, body)


@router.post(
    "/privacy/contributions",
    response_model=ContributionView,
    status_code=201,
    operation_id="createContribution",
)
async def contribution(
    request: Request,
    body: ContributionCreate,
    principal: Annotated[Principal, Depends(identity)],
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    return await service(request).contribute(principal, body, key)


@router.post(
    "/privacy/deletion-requests",
    response_model=DeletionView,
    status_code=202,
    operation_id="createDeletionRequest",
)
async def deletion(
    request: Request,
    body: DeletionCreate,
    principal: Annotated[Principal, Depends(identity)],
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    return await service(request).create(principal, body, key)


@router.get(
    "/privacy/deletion-requests/{request_id}",
    response_model=DeletionView,
    operation_id="getDeletionRequest",
)
async def deletion_progress(
    request: Request,
    response: Response,
    request_id: UUID,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    receipt: Annotated[str | None, Security(receipt_header)] = None,
):
    response.headers["Cache-Control"] = "no-store"
    if receipt:
        if len(receipt) != 64:
            raise ApiError("not_found", 404)
        return await service(request).get(None, request_id, receipt)
    principal = await identity(request, response, credentials)
    return await service(request).get(principal, request_id)
