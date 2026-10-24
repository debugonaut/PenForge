from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.modules.accountability.service import font_identity
from app.modules.exports.schemas import ExportCreate, ExportCreated, ExportDownload, ExportResponse
from app.modules.exports.service import ExportsService

router = APIRouter(
    prefix="/api/v1",
    tags=["private font exports"],
    responses={
        code: {"model": ErrorEnvelope} for code in (400, 401, 403, 404, 409, 413, 422, 429, 503)
    },
)


def service(request: Request) -> ExportsService:
    value = getattr(request.app.state, "exports_service", None)
    if value is None:
        raise ApiError("dependency_unavailable", 503)
    return value


@router.post(
    "/projects/{project_id}/exports",
    status_code=202,
    response_model=ExportCreated,
    operation_id="createExports",
)
async def create_exports(
    project_id: UUID,
    body: ExportCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
):
    response.headers["Cache-Control"] = "no-store"
    return await service(request).create(principal, project_id, idempotency_key, body)


@router.get("/exports/{export_id}", response_model=ExportResponse, operation_id="getExport")
async def get_export(
    export_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(identity)],
):
    response.headers["Cache-Control"] = "no-store"
    return await service(request).get(principal, export_id)


@router.get(
    "/exports/{export_id}/download-url",
    response_model=ExportDownload,
    operation_id="getExportDownloadUrl",
)
async def download_export(
    export_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
):
    response.headers["Cache-Control"] = "no-store"
    return await service(request).download(principal, export_id)
