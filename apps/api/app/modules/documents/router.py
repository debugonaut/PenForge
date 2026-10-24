"""HTTP routes for saved Writing Desk documents; rules stay in the service."""

from typing import Annotated
from uuid import UUID

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.modules.accountability.service import font_identity
from app.modules.documents.schemas import (
    WritingDocumentCreate,
    WritingDocumentPage,
    WritingDocumentPatch,
    WritingDocumentResponse,
)
from app.modules.documents.service import DocumentsService
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

router = APIRouter(
    prefix="/api/v1/documents",
    tags=["documents"],
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


def service(request: Request) -> DocumentsService:
    result = getattr(request.app.state, "documents_service", None)
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


@router.get("", response_model=WritingDocumentPage, operation_id="listWritingDocuments")
async def list_documents(
    request: Request,
    principal: Annotated[Principal, Depends(identity)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> WritingDocumentPage:
    return WritingDocumentPage.model_validate(
        await service(request).list(principal, limit=limit, cursor=cursor)
    )


@router.post(
    "",
    response_model=WritingDocumentResponse,
    status_code=201,
    operation_id="createWritingDocument",
)
async def create_document(
    body: WritingDocumentCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> WritingDocumentResponse:
    replayed, document = await service(request).create(
        principal, idempotency_key=idempotency_key, body=body
    )
    response.status_code = status.HTTP_200_OK if replayed else status.HTTP_201_CREATED
    response.headers["ETag"] = f'"v{document["row_version"]}"'
    response.headers["Cache-Control"] = "no-store"
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return WritingDocumentResponse.model_validate(document)


@router.get(
    "/{document_id}",
    response_model=WritingDocumentResponse,
    operation_id="getWritingDocument",
)
async def get_document(
    document_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(identity)],
) -> WritingDocumentResponse:
    document = await service(request).get(principal, document_id)
    response.headers["ETag"] = f'"v{document["row_version"]}"'
    response.headers["Cache-Control"] = "no-store"
    return WritingDocumentResponse.model_validate(document)


@router.patch(
    "/{document_id}",
    response_model=WritingDocumentResponse,
    operation_id="updateWritingDocument",
)
async def update_document(
    document_id: UUID,
    body: WritingDocumentPatch,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(font_identity)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> WritingDocumentResponse:
    changes = body.model_dump(exclude_unset=True)
    if body.document is not None and "document" in changes:
        changes["document"] = body.document.model_dump(mode="json")
    document = await service(request).update(
        principal, document_id, if_match=if_match, changes=changes
    )
    response.headers["ETag"] = f'"v{document["row_version"]}"'
    response.headers["Cache-Control"] = "no-store"
    return WritingDocumentResponse.model_validate(document)


@router.delete(
    "/{document_id}",
    status_code=204,
    operation_id="deleteWritingDocument",
)
async def delete_document(
    document_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(identity)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    await service(request).delete(principal, document_id, if_match=if_match)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
