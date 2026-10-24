"""Authenticated review routes; persistence and approval live in GlyphsService."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.modules.accountability.service import font_identity
from app.modules.glyphs.schemas import (
    AlternateCreate,
    AlternateToggle,
    CandidatePage,
    CapabilitiesResponse,
    CoverageResponse,
    GlyphPage,
    GlyphPatch,
    GlyphResponse,
    PromptResponse,
    SelectCandidate,
    SnapshotCreate,
    SnapshotResponse,
    SpaceMetric,
    StyleProfileCreate,
    StyleProfileResponse,
)
from app.modules.glyphs.service import GlyphsService

router = APIRouter(
    prefix="/api/v1/projects",
    tags=["glyph review"],
    responses={
        status: {"model": ErrorEnvelope}
        for status in (400, 401, 403, 404, 409, 412, 413, 422, 428, 503)
    },
)


def service(request: Request) -> GlyphsService:
    result = getattr(request.app.state, "glyphs_service", None)
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


def headers(response: Response, value):
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = f'"v{value["row_version"]}"'


@router.get("/{project_id}/glyphs", response_model=GlyphPage, operation_id="listGlyphs")
async def list_glyphs(
    project_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(identity)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
):
    return await service(request).list(principal, project_id, limit=limit, cursor=cursor)


@router.post(
    "/{project_id}/glyphs/space", response_model=GlyphResponse, operation_id="createSpaceMetric"
)
async def space_metric(
    project_id: UUID,
    body: SpaceMetric,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
):
    result = await service(request).space(
        principal, project_id, advance_width=body.advance_width, idempotency_key=idempotency_key
    )
    headers(response, result)
    return result


@router.get(
    "/{project_id}/glyphs/{glyph_id}", response_model=GlyphResponse, operation_id="getGlyph"
)
async def get_glyph(
    project_id: UUID,
    glyph_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(identity)],
):
    result = await service(request).get(principal, project_id, glyph_id)
    headers(response, result)
    return result


@router.get(
    "/{project_id}/glyphs/{glyph_id}/candidates",
    response_model=CandidatePage,
    operation_id="listGlyphCandidates",
)
async def candidates(
    project_id: UUID,
    glyph_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(identity)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
):
    return await service(request).candidates(
        principal, project_id, glyph_id, limit=limit, cursor=cursor
    )


@router.post(
    "/{project_id}/glyphs/{glyph_id}/select-candidate",
    response_model=GlyphResponse,
    operation_id="selectGlyphCandidate",
)
async def select_candidate(
    project_id: UUID,
    glyph_id: UUID,
    body: SelectCandidate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    result = await service(request).select(
        principal,
        project_id,
        glyph_id,
        body=body,
        if_match=if_match,
        idempotency_key=idempotency_key,
    )
    headers(response, result)
    return result


@router.patch(
    "/{project_id}/glyphs/{glyph_id}", response_model=GlyphResponse, operation_id="updateGlyph"
)
async def update_glyph(
    project_id: UUID,
    glyph_id: UUID,
    body: GlyphPatch,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ] = None,
):
    result = await service(request).update(
        principal,
        project_id,
        glyph_id,
        body=body,
        if_match=if_match,
        idempotency_key=idempotency_key,
    )
    headers(response, result)
    return result


@router.post(
    "/{project_id}/snapshots",
    response_model=SnapshotResponse,
    status_code=201,
    operation_id="createMilestoneSnapshot",
)
async def create_snapshot(
    project_id: UUID,
    body: SnapshotCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
):
    response.headers["Cache-Control"] = "no-store"
    return await service(request).snapshot(
        principal, project_id, body=body, idempotency_key=idempotency_key
    )


@router.get(
    "/{project_id}/snapshots/{snapshot_id}",
    response_model=SnapshotResponse,
    operation_id="getMilestoneSnapshot",
)
async def get_snapshot(
    project_id: UUID,
    snapshot_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(identity)],
):
    return await service(request).get_snapshot(principal, project_id, snapshot_id)


@router.get(
    "/{project_id}/coverage", response_model=CoverageResponse, operation_id="getProjectCoverage"
)
async def coverage(
    project_id: UUID, request: Request, principal: Annotated[Principal, Depends(identity)]
):
    return await service(request).coverage(principal, project_id)


@router.get(
    "/{project_id}/capture-prompts", response_model=PromptResponse, operation_id="getCapturePrompts"
)
async def prompts(
    project_id: UUID, request: Request, principal: Annotated[Principal, Depends(identity)]
):
    return await service(request).prompts(principal, project_id)


@router.get(
    "/{project_id}/calibration/next",
    response_model=PromptResponse,
    operation_id="getNextCalibrationPrompt",
)
async def next_prompt(
    project_id: UUID, request: Request, principal: Annotated[Principal, Depends(identity)]
):
    return await service(request).prompts(principal, project_id)


@router.get(
    "/{project_id}/style-profile",
    response_model=StyleProfileResponse,
    operation_id="getStyleProfile",
)
async def style_profile(
    project_id: UUID, request: Request, principal: Annotated[Principal, Depends(identity)]
):
    return await service(request).style_profile(principal, project_id)


@router.post(
    "/{project_id}/style-profile",
    response_model=StyleProfileResponse,
    status_code=201,
    operation_id="measureStyleProfile",
)
async def measure_style(
    project_id: UUID,
    body: StyleProfileCreate,
    request: Request,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
):
    return await service(request).measure_style(
        principal, project_id, body.snapshot_id, idempotency_key
    )


@router.post(
    "/{project_id}/glyphs/{glyph_id}/alternates",
    response_model=GlyphResponse,
    operation_id="createAuthenticAlternate",
)
async def add_alternate(
    project_id: UUID,
    glyph_id: UUID,
    body: AlternateCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    result = await service(request).add_alternate(
        principal, project_id, glyph_id, body, if_match, idempotency_key
    )
    headers(response, result)
    return result


@router.patch(
    "/{project_id}/glyphs/{glyph_id}/alternate-enabled",
    response_model=GlyphResponse,
    operation_id="setAlternateEnabled",
)
async def enable_alternate(
    project_id: UUID,
    glyph_id: UUID,
    body: AlternateToggle,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
):
    result = await service(request).enable_alternate(
        principal, project_id, glyph_id, body.enabled, if_match
    )
    headers(response, result)
    return result


@router.get(
    "/{project_id}/capabilities",
    response_model=CapabilitiesResponse,
    operation_id="getProjectCapabilities",
)
async def capabilities(
    project_id: UUID, request: Request, principal: Annotated[Principal, Depends(identity)]
):
    return await service(request).capabilities(principal, project_id)
