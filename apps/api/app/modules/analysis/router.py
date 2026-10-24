"""Explicit owner-requested analysis and archived public template downloads."""

from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import FileResponse
from penforge_contracts import CalibrationDefinition
from penforge_contracts.templates import load_templates, template_digest

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.modules.accountability.service import font_identity
from app.modules.analysis.schemas import AnalysisCreate, AnalysisJobResponse, TemplatePage
from app.modules.analysis.service import AnalysisService, calibration_policy

router = APIRouter(
    prefix="/api/v1",
    tags=["extraction"],
    responses={s: {"model": ErrorEnvelope} for s in (400, 401, 403, 404, 409, 422, 429, 503)},
)
DIRECTORY = Path(__file__).resolve().parents[5] / "packages/contracts/templates"


def service(request: Request) -> AnalysisService:
    result = getattr(request.app.state, "analysis_service", None)
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


@router.post(
    "/projects/{project_id}/jobs",
    response_model=AnalysisJobResponse,
    status_code=202,
    operation_id="createAnalysisJob",
)
async def create(
    project_id: UUID,
    body: AnalysisCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
):
    replay, result = await service(request).create(principal, project_id, idempotency_key, body)
    response.headers["Cache-Control"] = "no-store"
    if replay:
        response.headers["Idempotency-Replayed"] = "true"
    return result


@router.get(
    "/projects/{project_id}/analysis-jobs/{job_id}",
    response_model=AnalysisJobResponse,
    operation_id="getAnalysisJob",
)
async def get(
    project_id: UUID,
    job_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(identity)],
):
    response.headers["Cache-Control"] = "no-store"
    return await service(request).get(principal, project_id, job_id)


@router.get("/templates", response_model=TemplatePage, operation_id="listTemplates")
async def templates(script_code: Literal["Latn", "Deva"] = "Latn"):
    return {
        "items": [
            dict(
                manifest=m.model_dump(mode="json"),
                manifest_sha256=template_digest(m),
                svg_url=f"/api/v1/templates/{m.template_id}/{m.template_version}/{m.page.index}.svg",
                pdf_url=f"/api/v1/templates/{m.template_id}/{m.template_version}/{m.page.index}.pdf",
            )
            for m in load_templates()
            if m.script_code == script_code
        ]
    }


@router.get(
    "/templates/{template_id}/{version}/{page_index}.{format}", operation_id="downloadTemplate"
)
async def download(template_id: str, version: str, page_index: int, format: Literal["svg", "pdf"]):
    manifest = next(
        (
            m
            for m in load_templates()
            if (m.template_id, m.template_version, m.page.index)
            == (template_id, version, page_index)
        ),
        None,
    )
    if manifest is None:
        raise ApiError("not_found", 404)
    path = DIRECTORY / f"{manifest.template_id}-{version}-page-{page_index}.{format}"
    if not path.is_file():
        raise ApiError("dependency_unavailable", 503)
    return FileResponse(
        path,
        media_type="application/pdf" if format == "pdf" else "image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


@router.get(
    "/calibration-definitions/{script_code}",
    response_model=CalibrationDefinition,
    operation_id="getCalibrationDefinition",
)
async def calibration(script_code: str):
    if script_code == "Deva":
        return CalibrationDefinition.model_validate_json(
            (DIRECTORY.parent / "policies/deva-calibration.v1.json").read_bytes()
        )
    if script_code != "Latn":
        raise ApiError("not_found", 404)
    return calibration_policy()["definition"]
