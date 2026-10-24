"""HTTP project routes; all persistence rules stay in ProjectsService."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.modules.accountability.service import font_identity
from app.modules.privacy.service import DeletionView, project_deletion
from app.modules.projects.schemas import ProjectCreate, ProjectPage, ProjectPatch, ProjectResponse
from app.modules.projects.service import ProjectsService

router = APIRouter(
    prefix="/api/v1/projects",
    tags=["projects"],
    responses={
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        412: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
        428: {"model": ErrorEnvelope},
        503: {"model": ErrorEnvelope},
    },
)


def service(request: Request) -> ProjectsService:
    result = request.app.state.projects_service
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


@router.post("", response_model=ProjectResponse, status_code=201, operation_id="createProject")
async def create_project(
    body: ProjectCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> ProjectResponse:
    result = await service(request).create(
        principal,
        idempotency_key=idempotency_key,
        name=body.name,
        script_code=body.script_code,
        style_controls=body.style_controls.model_dump(exclude_none=True),
    )
    replayed, status, project = result
    response.status_code = status
    response.headers["ETag"] = f'"v{project["row_version"]}"'
    response.headers["Cache-Control"] = "no-store"
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return ProjectResponse.model_validate(project)


@router.get("", response_model=ProjectPage, operation_id="listProjects")
async def list_projects(
    request: Request,
    principal: Annotated[Principal, Depends(identity)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> ProjectPage:
    result = await service(request).list(principal, limit=limit, cursor=cursor)
    return ProjectPage.model_validate(result)


@router.get("/{project_id}", response_model=ProjectResponse, operation_id="getProject")
async def get_project(
    project_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(identity)],
) -> ProjectResponse:
    project = await service(request).get(principal, project_id)
    response.headers["ETag"] = f'"v{project["row_version"]}"'
    response.headers["Cache-Control"] = "no-store"
    return ProjectResponse.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectResponse, operation_id="updateProject")
async def update_project(
    project_id: UUID,
    body: ProjectPatch,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ProjectResponse:
    changes = body.model_dump(exclude_unset=True)
    if body.style_controls is not None and "style_controls" in changes:
        changes["style_controls"] = body.style_controls.model_dump(exclude_none=True)
    project = await service(request).update(
        principal, project_id, if_match=if_match, changes=changes
    )
    response.headers["ETag"] = f'"v{project["row_version"]}"'
    response.headers["Cache-Control"] = "no-store"
    return ProjectResponse.model_validate(project)


@router.delete(
    "/{project_id}",
    response_model=DeletionView,
    responses={202: {"description": "Private deletion accepted; use privacy progress endpoint."}},
    status_code=202,
    operation_id="requestProjectDeletion",
)
async def request_project_deletion(
    project_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(identity)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ErrorEnvelope | JSONResponse:
    privacy = getattr(request.app.state, "privacy_service", None)
    if privacy is None:
        await service(request).request_delete(principal, project_id, if_match=if_match)
        raise ApiError("capability_unavailable", 503)
    from fastapi.encoders import jsonable_encoder

    result = await project_deletion(privacy, principal, project_id, if_match)
    return JSONResponse(status_code=202, content=jsonable_encoder(result))
