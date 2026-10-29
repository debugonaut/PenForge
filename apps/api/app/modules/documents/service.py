"""Document rules: owner isolation, idempotent create, strong ETags, signed cursors."""

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.adapters.auth.base import Principal
from app.core.errors import ApiError
from app.modules.documents.repository import DocumentsRepository
from app.modules.documents.schemas import WritingDocumentCreate
from app.modules.identity.service import IdentityService
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


class DocumentsService:
    def __init__(
        self,
        engine: AsyncEngine,
        identity_service: IdentityService,
        *,
        cursor_secret: bytes,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ids: Callable[[], UUID] = uuid4,
        idempotency_ttl_seconds: int = 86400,
    ):
        if len(cursor_secret) < 32:
            raise ValueError("cursor signing key must contain at least 32 bytes")
        self.engine = engine
        self.identity_service = identity_service
        self.cursor_secret = cursor_secret
        self.clock = clock
        self.ids = ids
        self.idempotency_ttl_seconds = idempotency_ttl_seconds

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncConnection]:
        try:
            async with self.engine.begin() as connection:
                yield connection
        except (OperationalError, InterfaceError, TimeoutError):
            raise ApiError("dependency_unavailable", 503) from None

    @staticmethod
    def etag(row_version: int) -> str:
        return f'"v{row_version}"'

    @staticmethod
    def _version(if_match: str | None) -> int:
        if if_match is None:
            raise ApiError("precondition_required", 428)
        match = re.fullmatch(r'"v([1-9][0-9]{0,8})"', if_match)
        if match is None:
            raise ApiError("revision_conflict", 412)
        return int(match.group(1))

    @staticmethod
    def _dto(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "title": row["title"],
            "format": row["format"],
            "document": dict(row["document"]),
            "row_version": row["row_version"],
            "created_at": row["created_at"].astimezone(UTC),
            "updated_at": row["updated_at"].astimezone(UTC),
        }

    def _encode_cursor(self, owner_id: UUID, updated_at: datetime, document_id: UUID) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "kind": "doc",
                "owner": str(owner_id),
                "updated_at": updated_at.astimezone(UTC).isoformat(timespec="microseconds"),
                "id": str(document_id),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest = hmac.new(self.cursor_secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + digest).rstrip(b"=").decode("ascii")

    def _decode_cursor(self, owner_id: UUID, cursor: str | None) -> tuple[datetime, UUID] | None:
        if cursor is None:
            return None
        try:
            if len(cursor) > 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
                raise ValueError
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload, supplied_mac = raw[:-32], raw[-32:]
            expected_mac = hmac.new(self.cursor_secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied_mac, expected_mac):
                raise ValueError
            decoded = json.loads(payload)
            if set(decoded) != {"v", "kind", "owner", "updated_at", "id"}:
                raise ValueError
            if decoded["v"] != 1 or decoded["kind"] != "doc" or decoded["owner"] != str(owner_id):
                raise ValueError
            updated_at = datetime.fromisoformat(decoded["updated_at"])
            if updated_at.tzinfo is None:
                raise ValueError
            return updated_at.astimezone(UTC), UUID(decoded["id"])
        except (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            binascii.Error,
            json.JSONDecodeError,
        ):
            raise ApiError("validation_error", 400, details={"fields": ["/query/cursor"]}) from None

    async def _identity(self, connection: AsyncConnection, principal: Principal) -> Any:
        return await self.identity_service.resolve_in_transaction(
            connection, principal, self.clock()
        )

    @staticmethod
    def _create_hash(body: WritingDocumentCreate) -> bytes:
        canonical = json.dumps(
            body.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request = "POST\n/api/v1/documents\n" + canonical
        return hashlib.sha256(request.encode("utf-8")).digest()

    async def list(self, principal: Principal, *, limit: int, cursor: str | None) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            owner_id = user["id"]
            before = self._decode_cursor(owner_id, cursor)
            rows = await DocumentsRepository(connection).list_page(owner_id, limit, before)
            more = len(rows) > limit
            page = rows[:limit]
            next_cursor = (
                self._encode_cursor(owner_id, page[-1]["updated_at"], page[-1]["id"])
                if more and page
                else None
            )
            return {"items": [self._dto(row) for row in page], "next_cursor": next_cursor}

    async def create(
        self,
        principal: Principal,
        *,
        idempotency_key: str,
        body: WritingDocumentCreate,
    ) -> tuple[bool, dict[str, Any]]:
        if not 1 <= len(idempotency_key) <= 255 or any(ord(ch) < 33 for ch in idempotency_key):
            raise ApiError("validation_error", 400, details={"fields": ["/header/idempotency-key"]})
        now = self.clock()
        expires_at = now + timedelta(seconds=self.idempotency_ttl_seconds)
        request_hash = self._create_hash(body)
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            owner_id = user["id"]
            repo = DocumentsRepository(connection)
            inserted, existing = await repo.claim_idempotency(
                owner_id, idempotency_key, request_hash, now, expires_at
            )
            if not inserted:
                if existing is None:
                    raise ApiError("dependency_unavailable", 503)
                if existing["request_hash"] != request_hash:
                    raise ApiError("idempotency_key_reuse", 422)
                if existing["state"] == "in_progress":
                    raise ApiError("idempotency_in_progress", 409, headers={"Retry-After": "1"})
                if existing["response_body"] is None:
                    raise ApiError("dependency_unavailable", 503)
                return True, existing["response_body"]
            row = await repo.create(
                document_id=self.ids(),
                owner_id=owner_id,
                title=body.title,
                format_=body.format,
                document=body.document.model_dump(mode="json"),
                now=now,
            )
            dto = self._dto(row)
            replay = json.loads(json.dumps(dto, default=str))
            await repo.complete_idempotency(owner_id, idempotency_key, 201, replay, expires_at)
            return False, replay

    async def get(self, principal: Principal, document_id: UUID) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            row = await DocumentsRepository(connection).get(user["id"], document_id)
            if row is None:
                raise ApiError("not_found", 404)
            return self._dto(row)

    async def update(
        self,
        principal: Principal,
        document_id: UUID,
        *,
        if_match: str | None,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        version = self._version(if_match)
        if not changes:
            raise ApiError("validation_error", 400, details={"fields": ["/body"]})
        now = self.clock()
        values: dict[str, Any] = {}
        if "title" in changes:
            values["title"] = changes["title"]
        if "format" in changes:
            values["format"] = changes["format"]
        if "document" in changes:
            values["document"] = changes["document"]
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            # Validate title-only patches against retained ranges, and body-only
            # patches against the actual title. The subsequent CAS fences races.
            current = await DocumentsRepository(connection).get(user["id"], document_id)
            if current is None:
                raise ApiError("not_found", 404)
            if current["row_version"] != version:
                raise ApiError(
                    "revision_conflict",
                    412,
                    context={
                        "current_row_version": current["row_version"],
                        "updated_at": current["updated_at"].astimezone(UTC).isoformat(),
                    },
                )
            try:
                WritingDocumentCreate.model_validate(
                    {
                        "title": values.get("title", current["title"]),
                        "format": values.get("format", current["format"]),
                        "document": values.get("document", current["document"]),
                    }
                )
            except ValueError:
                raise ApiError(
                    "validation_error", 400, details={"fields": ["/body/document/text_styles"]}
                ) from None
            row = await DocumentsRepository(connection).compare_and_set(
                user["id"], document_id, version, values, now
            )
            if row is None:
                current = await DocumentsRepository(connection).get(user["id"], document_id)
                if current is None:
                    raise ApiError("not_found", 404)
                raise ApiError(
                    "revision_conflict",
                    412,
                    context={
                        "current_row_version": current["row_version"],
                        "updated_at": current["updated_at"].astimezone(UTC).isoformat(),
                    },
                )
            return self._dto(row)

    async def delete(
        self, principal: Principal, document_id: UUID, *, if_match: str | None
    ) -> None:
        version = self._version(if_match)
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            removed = await DocumentsRepository(connection).delete(user["id"], document_id, version)
            if not removed:
                current = await DocumentsRepository(connection).get(user["id"], document_id)
                if current is None:
                    raise ApiError("not_found", 404)
                raise ApiError("revision_conflict", 412)
