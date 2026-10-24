import re
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    request_id: str
    retryable: bool = False
    details: dict[str, list[str]] = {}
    context: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    def __init__(
        self,
        code: Literal[
            "unauthenticated",
            "invalid_token",
            "dependency_unavailable",
            "not_found",
            "account_suspended",
            "account_deleting",
            "account_merged",
            "already_claimed",
            "stale_guest_token",
            "invalid_claim",
            "rate_limited",
            "state_conflict",
            "guest_limit_reached",
            "idempotency_in_progress",
            "idempotency_key_reuse",
            "precondition_required",
            "revision_conflict",
            "capability_unavailable",
            "validation_error",
            "payload_too_large",
            "quota_unconfigured",
            "quota_exceeded",
            "upload_expired",
            "upload_issuance_disabled",
            "object_missing",
            "upload_mismatch",
            "template_mismatch",
            "unsupported_media_type",
            "capacity_exceeded",
            "checkpoint_invalid",
            "recent_auth_required",
            "consent_required",
            "consent_policy_conflict",
            "consent_ineligible",
            "account_required",
            "email_verification_required",
            "terms_required",
            "policy_changed",
            "service_not_ready",
        ],
        status: int,
        *,
        headers: dict[str, str] | None = None,
        details: dict[str, list[str]] | None = None,
        context: dict[str, Any] | None = None,
    ):
        self.code = code
        self.status = status
        self.headers = headers or {}
        self.details = details or {}
        self.context = context


MESSAGES = {
    "account_required": "Create an account before using this service.",
    "email_verification_required": "Verify your account email before continuing.",
    "terms_required": "Review and accept the current service terms.",
    "policy_changed": "Review the updated service terms before accepting.",
    "service_not_ready": "The service is not ready for public use.",
    "recent_auth_required": "Sign in again before changing consent or requesting deletion.",
    "consent_required": "An active matching model-training consent is required.",
    "consent_policy_conflict": "Review the current consent policy before continuing.",
    "consent_ineligible": "Training contributions require an eligible registered account.",
    "quota_unconfigured": "Resource capacity has not been configured. Keep your work and retry later.",
    "quota_exceeded": "Resource capacity is currently exhausted. Keep your work and retry later.",
    "capacity_exceeded": "Qualified processing capacity is temporarily unavailable. Keep your work and retry later.",
    "checkpoint_invalid": "Saved processing output could not be verified. Your original is retained.",
    "upload_issuance_disabled": "New uploads are temporarily paused. Keep your capture locally and retry later.",
    "upload_expired": "This upload expired. Keep your capture and start a new upload.",
    "object_missing": "Upload the original file before completing this capture.",
    "upload_mismatch": "The uploaded bytes do not match the declared capture.",
    "template_mismatch": "Use the matching template version or retake this as a freeform capture.",
    "unsupported_media_type": "Use a JPEG, PNG or WebP image.",
    "unauthenticated": "Authentication is required.",
    "invalid_token": "Authentication could not be verified.",
    "dependency_unavailable": "A required service is unavailable.",
    "not_found": "Resource not found.",
    "validation_error": "Request validation failed.",
    "internal_error": "The request could not be completed.",
    "payload_too_large": "The request exceeds the size limit.",
    "http_error": "The request could not be completed.",
    "account_suspended": "This account is suspended.",
    "account_deleting": "This account is being deleted.",
    "account_merged": "This guest account has already been claimed. Sign in to the destination account.",
    "already_claimed": "This guest account has already been claimed.",
    "stale_guest_token": "Sign in to the guest account again before claiming its work.",
    "invalid_claim": "These identities cannot be used for this claim.",
    "rate_limited": "Too many requests. Try again shortly.",
    "state_conflict": "The account state changed. Try again.",
    "guest_limit_reached": "This guest account has used its three project creations.",
    "idempotency_in_progress": "This create request is still being completed. Retry shortly.",
    "idempotency_key_reuse": "This idempotency key was already used for a different request.",
    "precondition_required": "Fetch the latest project version before saving changes.",
    "revision_conflict": "This project changed in another session. Review the current version before saving.",
    "capability_unavailable": "This capability is currently unavailable.",
}


def error_response(
    request: Request,
    code: str,
    status: int,
    *,
    fields: list[str] | None = None,
    headers: dict[str, str] | None = None,
    details: dict[str, list[str]] | None = None,
    context: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ErrorBody(
        code=code,
        message=MESSAGES[code],
        request_id=getattr(request.state, "request_id", "unavailable"),
        retryable=status in (429, 503),
    )
    if fields is not None:
        body.details = {"fields": fields}
    if details is not None:
        body.details = details
    if context is not None:
        body.context = context
    return JSONResponse(
        ErrorEnvelope(error=body).model_dump(),
        status_code=status,
        headers={"Cache-Control": "no-store", **(headers or {})},
    )


def validation_pointers(request: Request, exc: RequestValidationError) -> list[str]:
    """Only reflect schema-declared names, never arbitrary submitted keys or input values."""
    route = request.scope.get("route")
    body_field = getattr(route, "body_field", None)
    model = getattr(getattr(body_field, "field_info", None), "annotation", None)
    get_schema = getattr(model, "model_json_schema", None)
    schema = get_schema() if callable(get_schema) else {}
    definitions = schema.get("$defs", {})
    pointers = set()
    for error in exc.errors():
        location = error.get("loc", ())
        source = (
            location[0]
            if location and location[0] in {"body", "query", "path", "header", "cookie"}
            else "body"
        )
        pointer = "/" + source
        node: dict[str, Any] = schema if source == "body" else {}
        for part in location[1:]:
            if "$ref" in node:
                node = definitions.get(node["$ref"].split("/")[-1], {})
            if (
                isinstance(part, str)
                and not re.search(
                    r"token|secret|password|authorization|outline|image|pixels|text", part, re.I
                )
                and part in node.get("properties", {})
            ):
                node = node["properties"][part]
                pointer += "/" + part.replace("~", "~0").replace("/", "~1")
            elif type(part) is int and 0 <= part <= 100000 and "items" in node:
                node = node["items"]
                pointer += "/" + str(part)
            else:
                break
        pointers.add(pointer)
    return sorted(pointers) or ["/body"]


def register_errors(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        metrics = getattr(request.app.state, "metrics", None)
        if metrics is not None:
            metrics.rejection(exc.code)
        return error_response(
            request,
            exc.code,
            exc.status,
            headers=exc.headers,
            details=exc.details,
            context=exc.context,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Neither input values nor arbitrary field names/context are reflected.
        return error_response(
            request, "validation_error", 400, fields=validation_pointers(request, exc)
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return error_response(request, code, exc.status_code)
