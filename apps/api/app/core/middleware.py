import time
from uuid import UUID, uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response
from app.core.logging import log_event
from app.core.metrics import Metrics, route_class


class RequestMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int,
        upload_max_bytes: int | None = None,
        metrics: Metrics | None = None,
    ):
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes or max_bytes
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        raw_id = request.headers.get("x-request-id", "")
        try:
            request_id = str(UUID(raw_id)) if len(raw_id) == 36 else str(uuid4())
        except ValueError:
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = False
        status = 500
        begin = time.monotonic()
        buffered: list[Message] = []
        received_bytes = sent_bytes = 0
        maximum = (
            self.upload_max_bytes
            if scope["method"] == "PUT" and scope["path"].startswith("/api/v1/storage/objects/")
            else self.max_bytes
        )

        async def buffered_receive() -> Message:
            return buffered.pop(0) if buffered else await receive()

        async def response_send(message: Message) -> None:
            nonlocal started, status, sent_bytes
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
                if scope["path"].startswith("/api/v1/") and scope["path"] not in {
                    "/api/v1/health",
                    "/api/v1/contracts",
                }:
                    message["headers"] = [
                        (key, value)
                        for key, value in message["headers"]
                        if key.lower() != b"cache-control"
                    ] + [(b"cache-control", b"no-store")]
            if message["type"] == "http.response.body":
                sent_bytes += len(message.get("body", b""))
            await send(message)

        try:
            length = request.headers.get("content-length")
            if length and (not length.isdecimal() or int(length) > maximum):
                raise BodyTooLarge
            # Enforce chunked limits before downstream body parsing can turn an
            # over-budget exception into a generic malformed-JSON response.
            size = 0
            while True:
                message = await receive()
                buffered.append(message)
                size += len(message.get("body", b""))
                received_bytes = size
                if size > maximum:
                    raise BodyTooLarge
                if message["type"] != "http.request" or not message.get("more_body", False):
                    break
            await self.app(scope, buffered_receive, response_send)
        except Exception as exc:
            if started:
                raise
            code = "payload_too_large" if isinstance(exc, BodyTooLarge) else "internal_error"
            if self.metrics is not None:
                self.metrics.rejection(
                    "body_too_large" if isinstance(exc, BodyTooLarge) else "unhandled_exception"
                )
            response = error_response(request, code, 413 if isinstance(exc, BodyTooLarge) else 500)
            await response(scope, receive, response_send)
        finally:
            elapsed = time.monotonic() - begin
            if self.metrics is not None:
                self.metrics.request(route_class(scope["path"]), status, elapsed)
                self.metrics.transfer(received_bytes, sent_bytes)
            log_event(
                "request_completed",
                request_id=request_id,
                status_code=status,
                duration_ms=round(elapsed * 1000, 3),
            )


class BodyTooLarge(Exception):
    pass
