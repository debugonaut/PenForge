"""HTTP capture routes. Authentication, quota and publication rules stay in services."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.modules.accountability.service import font_identity
from app.modules.uploads.schemas import (
    AssetResponse,
    CapabilityResponse,
    SampleCreate,
    SamplePage,
    SampleResponse,
    UploadComplete,
    UploadInit,
    UploadResponse,
)
from app.modules.uploads.service import UploadsService

router = APIRouter(
    prefix="/api/v1",
    tags=["capture"],
    responses={
        status: {"model": ErrorEnvelope}
        for status in (400, 401, 403, 404, 409, 413, 415, 422, 429, 503)
    },
)


def service(request: Request) -> UploadsService:
    result = getattr(request.app.state, "uploads_service", None)
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


@router.post(
    "/projects/{project_id}/uploads",
    response_model=UploadResponse,
    status_code=201,
    operation_id="initializeUpload",
)
async def initialize_upload(
    project_id: UUID,
    body: UploadInit,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> UploadResponse:
    replayed, result = await service(request).initialize(
        principal, project_id, idempotency_key=idempotency_key, body=body
    )
    response.headers["Cache-Control"] = "no-store"
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return UploadResponse.model_validate(result)


@router.post(
    "/uploads/{asset_id}/upload-url",
    response_model=UploadResponse,
    operation_id="renewUploadCapability",
)
async def renew_upload(
    asset_id: UUID, request: Request, principal: Annotated[Principal, Depends(font_identity)]
) -> UploadResponse:
    return UploadResponse.model_validate(await service(request).renew_upload(principal, asset_id))


@router.post(
    "/uploads/{asset_id}/complete",
    response_model=UploadComplete,
    status_code=202,
    operation_id="completeUpload",
)
async def complete_upload(
    asset_id: UUID, request: Request, principal: Annotated[Principal, Depends(font_identity)]
) -> UploadComplete:
    return UploadComplete.model_validate(await service(request).complete(principal, asset_id))


@router.get("/assets/{asset_id}", response_model=AssetResponse, operation_id="getAsset")
async def get_asset(
    asset_id: UUID, request: Request, principal: Annotated[Principal, Depends(identity)]
) -> AssetResponse:
    return AssetResponse.model_validate(await service(request).get(principal, asset_id))


@router.get(
    "/assets/{asset_id}/download-url",
    response_model=CapabilityResponse,
    operation_id="getAssetDownloadCapability",
)
async def download_asset(
    asset_id: UUID, request: Request, principal: Annotated[Principal, Depends(font_identity)]
) -> CapabilityResponse:
    return CapabilityResponse.model_validate(await service(request).download(principal, asset_id))


@router.post(
    "/projects/{project_id}/samples",
    response_model=SampleResponse,
    status_code=201,
    operation_id="registerSample",
)
async def register_sample(
    project_id: UUID,
    body: SampleCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> SampleResponse:
    replayed, result = await service(request).register_sample(
        principal, project_id, idempotency_key=idempotency_key, body=body
    )
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return SampleResponse.model_validate(result)


@router.get("/projects/{project_id}/samples", response_model=SamplePage, operation_id="listSamples")
async def list_samples(
    project_id: UUID, request: Request, principal: Annotated[Principal, Depends(identity)]
) -> SamplePage:
    return SamplePage.model_validate(await service(request).list_samples(principal, project_id))


@router.put(
    "/storage/objects/{capability}",
    status_code=204,
    operation_id="putLocalObject",
    include_in_schema=False,
)
async def put_local_object(
    capability: str, request: Request, principal: Annotated[Principal, Depends(font_identity)]
) -> Response:
    await service(request).authorize_capability(principal, capability, "PUT")
    # Bound actual receive bytes as well as Content-Length. Ordinary JSON routes
    # retain their smaller middleware limit; no multipart/parser is involved here.
    maximum = request.app.state.settings.upload_max_bytes
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > maximum or int(declared) < 0:
                raise ApiError("payload_too_large", 413)
        except ValueError:
            raise ApiError("validation_error", 400) from None
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > maximum:
            raise ApiError("payload_too_large", 413)
        data.extend(chunk)
    await service(request).put_capability(capability, bytes(data))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/storage/objects/{capability}", operation_id="getLocalObject", include_in_schema=False)
async def get_local_object(
    capability: str, request: Request, principal: Annotated[Principal, Depends(font_identity)]
) -> Response:
    await service(request).authorize_capability(principal, capability, "GET")
    data, mime = await service(request).read_capability(capability)
    return Response(
        data,
        media_type=mime,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
        },
    )
