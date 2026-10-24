"""Owner-only polling baseline and bounded, resumable server-polled SSE."""

import asyncio
import json
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse
from penforge_contracts import content_hash

from app.adapters.auth.base import Principal, parse_bearer_token
from app.adapters.auth.dependencies import identity
from app.core.errors import ApiError, ErrorEnvelope
from app.modules.jobs.schemas import JobStatus
from app.modules.jobs.service import TERMINAL, JobsService

router = APIRouter(
    prefix="/api/v1",
    tags=["jobs"],
    responses={status: {"model": ErrorEnvelope} for status in (400, 401, 403, 404, 409, 429, 503)},
)


def service(request):
    uploads = request.app.state.uploads_service
    if uploads is None:
        raise ApiError("dependency_unavailable", 503)
    return JobsService(uploads)


@router.get("/jobs/{job_id}", response_model=JobStatus, operation_id="getVerificationJob")
async def get_job(
    job_id: UUID,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(identity)],
    if_none_match: Annotated[str | None, Header()] = None,
):
    result = await service(request).get(principal, job_id)
    tag = '"' + content_hash(result)[7:] + '"'
    headers = {
        "ETag": tag,
        "Cache-Control": "private, no-cache",
        "Retry-After": "30"
        if result["state"] in TERMINAL
        else "5"
        if result["state"] == "queued"
        else "2",
    }
    if if_none_match == tag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return JobStatus.model_validate(result)


@router.post(
    "/jobs/{job_id}/cancel", response_model=JobStatus, status_code=202, operation_id="cancelJob"
)
async def cancel_job(
    job_id: UUID, request: Request, principal: Annotated[Principal, Depends(identity)]
):
    return JobStatus.model_validate(await service(request).cancel(principal, job_id))


@router.get(
    "/jobs/{job_id}/events",
    operation_id="getJobEvents",
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def job_events(
    job_id: UUID,
    request: Request,
    principal: Annotated[Principal, Depends(identity)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", max_length=20)] = None,
):
    if last_event_id is not None and (
        not last_event_id.isdecimal() or int(last_event_id) > 2**63 - 1
    ):
        raise ApiError("validation_error", 400)
    after = int(last_event_id or 0)
    jobs = service(request)
    stream_id, lifetime = await jobs.open_stream(principal, job_id, after)
    token = parse_bearer_token(request.headers["authorization"])

    async def stream():
        cursor = after
        started = heartbeat_at = time.monotonic()
        try:
            while time.monotonic() - started < lifetime:
                if await request.is_disconnected():
                    return
                try:
                    # Token revocation, account state AND ownership/project state are rechecked.
                    fresh = await request.app.state.auth_verifier.verify(token, check_revoked=True)
                    if fresh.firebase_uid != principal.firebase_uid:
                        raise ApiError("invalid_token", 401)
                    rows = await jobs.events(fresh, job_id, cursor)
                except ApiError:
                    yield 'event: revoked\ndata: {"code":"access_revoked"}\n\n'
                    return
                for row in rows:
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: status\ndata: {json.dumps(row['state_snapshot'], separators=(',', ':'))}\n\n"
                if rows and rows[-1]["state_snapshot"]["state"] in TERMINAL:
                    return
                if time.monotonic() - heartbeat_at >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat_at = time.monotonic()
                await asyncio.sleep(1.5)
        finally:
            await jobs.close_stream(stream_id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )
