"""HTTP routes for bounded project working state; rules stay in the service."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.modules.accountability.service import font_identity
from app.modules.workspaces.schemas import WorkspacePut, WorkspaceResponse
from app.modules.workspaces.service import WorkspacesService

router = APIRouter(
    prefix="/api/v1/projects",
    tags=["workspaces"],
    responses={
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        412: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
        428: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)


def service(request: Request) -> WorkspacesService:
    result = getattr(request.app.state, "workspaces_service", None)
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


@router.get(
    "/{project_id}/workspace",
    response_model=WorkspaceResponse,
    operation_id="getProjectWorkspace",
)
async def get_workspace(
    project_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(identity)],
) -> WorkspaceResponse:
    workspace = await service(request).get(principal, project_id)
    response.headers["ETag"] = f'"v{workspace["row_version"]}"'
    response.headers["Cache-Control"] = "no-store"
    return WorkspaceResponse.model_validate(workspace)


@router.put(
    "/{project_id}/workspace",
    response_model=WorkspaceResponse,
    operation_id="putProjectWorkspace",
)
async def put_workspace(
    project_id: UUID,
    body: WorkspacePut,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> WorkspaceResponse:
    workspace = await service(request).put(
        principal,
        project_id,
        if_match=if_match,
        workspace=body.workspace,
    )
    response.headers["ETag"] = f'"v{workspace["row_version"]}"'
    response.headers["Cache-Control"] = "no-store"
    return WorkspaceResponse.model_validate(workspace)
