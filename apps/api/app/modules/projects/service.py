"""Project rules, idempotency, owner scoping and optimistic concurrency."""

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

from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.adapters.auth.base import Principal
from app.core.errors import ApiError
from app.modules.identity.service import IdentityService
from app.modules.projects.repository import ProjectsRepository


class ProjectsService:
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
        if not 60 <= idempotency_ttl_seconds <= 604800:
            raise ValueError("idempotency TTL must be between one minute and seven days")
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
    def _dto(row: Any) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "name": row["name"],
            "script_code": row["script_code"],
            "status": row["status"],
            "active_snapshot_id": (
                str(row["active_snapshot_id"]) if row["active_snapshot_id"] is not None else None
            ),
            "style_controls": dict(row["style_controls"] or {}),
            "row_version": row["row_version"],
            # PostgreSQL timestamptz is returned in the connection's session
            # timezone. The public contract is UTC regardless of host locale.
            "created_at": row["created_at"].astimezone(UTC),
            "updated_at": row["updated_at"].astimezone(UTC),
        }

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

    def _encode_cursor(self, owner_id: UUID, updated_at: datetime, project_id: UUID) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "owner": str(owner_id),
                "updated_at": updated_at.astimezone(UTC).isoformat(timespec="microseconds"),
                "id": str(project_id),
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
            if set(decoded) != {"v", "owner", "updated_at", "id"}:
                raise ValueError
            if decoded["v"] != 1 or decoded["owner"] != str(owner_id):
                raise ValueError
            updated_at = datetime.fromisoformat(decoded["updated_at"])
            project_id = UUID(decoded["id"])
            if updated_at.tzinfo is None:
                raise ValueError
            return updated_at.astimezone(UTC), project_id
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
    def _create_hash(name: str, script_code: str, style_controls: dict[str, float]) -> bytes:
        canonical = json.dumps(
            {"name": name, "script_code": script_code, "style_controls": style_controls},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request = "POST\n/api/v1/projects\n" + canonical
        return hashlib.sha256(request.encode("utf-8")).digest()

    async def create(
        self,
        principal: Principal,
        *,
        idempotency_key: str,
        name: str,
        script_code: str,
        style_controls: dict[str, float],
    ) -> tuple[bool, int, dict[str, Any]]:
        if not 1 <= len(idempotency_key) <= 255 or any(ord(ch) < 33 for ch in idempotency_key):
            raise ApiError("validation_error", 400, details={"fields": ["/header/idempotency-key"]})
        now = self.clock()
        expires_at = now + timedelta(seconds=self.idempotency_ttl_seconds)
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            owner_id = user["id"]
            repo = ProjectsRepository(connection)
            await repo.reap_expired_idempotency(now)
            inserted, existing = await repo.claim_idempotency(
                owner_id,
                idempotency_key,
                self._create_hash(name, script_code, style_controls),
                now,
                expires_at,
            )
            if not inserted:
                if existing is None:
                    raise ApiError("dependency_unavailable", 503)
                if existing["request_hash"] != self._create_hash(name, script_code, style_controls):
                    raise ApiError("idempotency_key_reuse", 422)
                if existing["state"] == "in_progress":
                    raise ApiError("idempotency_in_progress", 409, headers={"Retry-After": "1"})
                if existing["response_status"] is None or existing["response_body"] is None:
                    raise ApiError("dependency_unavailable", 503)
                return True, existing["response_status"], existing["response_body"]

            if user["is_anonymous"] and not await repo.consume_guest_creation(owner_id, now):
                raise ApiError("guest_limit_reached", 403)
            row = await repo.create(
                project_id=self.ids(),
                owner_id=owner_id,
                name=name,
                script_code=script_code,
                style_controls=style_controls,
                now=now,
            )
            body = self._dto(row)
            replay_body = json.loads(json.dumps(body, default=str))
            await repo.complete_idempotency(
                owner_id,
                idempotency_key,
                201,
                replay_body,
                now,
                expires_at,
            )
            return False, 201, replay_body

    async def get(self, principal: Principal, project_id: UUID) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            row = await ProjectsRepository(connection).get(user["id"], project_id)
            if row is None:
                raise ApiError("not_found", 404)
            return self._dto(row)

    async def list(
        self,
        principal: Principal,
        *,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            owner_id = user["id"]
            before = self._decode_cursor(owner_id, cursor)
            rows = await ProjectsRepository(connection).list_page(owner_id, limit, before)
            more = len(rows) > limit
            page = rows[:limit]
            next_cursor = (
                self._encode_cursor(owner_id, page[-1]["updated_at"], page[-1]["id"])
                if more and page
                else None
            )
            return {"items": [self._dto(row) for row in page], "next_cursor": next_cursor}

    async def _owned_and_versioned(
        self,
        connection: AsyncConnection,
        owner_id: UUID,
        project_id: UUID,
        if_match: str | None,
    ) -> tuple[Any, int]:
        repo = ProjectsRepository(connection)
        current = await repo.get(owner_id, project_id)
        if current is None:
            raise ApiError("not_found", 404)
        version = self._version(if_match)
        if current["status"] == "deleting":
            raise ApiError("state_conflict", 409)
        if current["row_version"] != version:
            raise ApiError(
                "revision_conflict",
                412,
                context={
                    "current_row_version": current["row_version"],
                    "current_project": {
                        "id": str(current["id"]),
                        "name": current["name"],
                        "script_code": current["script_code"],
                        "status": current["status"],
                        "updated_at": current["updated_at"].astimezone(UTC).isoformat(),
                        "row_version": current["row_version"],
                    },
                },
            )
        return current, version

    async def update(
        self,
        principal: Principal,
        project_id: UUID,
        *,
        if_match: str | None,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        if not changes:
            raise ApiError("validation_error", 400, details={"fields": ["/body"]})
        if any(value is None for value in changes.values()):
            raise ApiError("validation_error", 400, details={"fields": ["/body"]})
        now = self.clock()
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            current, version = await self._owned_and_versioned(
                connection, user["id"], project_id, if_match
            )
            values: dict[str, Any] = {}
            if "name" in changes:
                values["name"] = changes["name"]
            if "style_controls" in changes:
                values["style_controls"] = {
                    **dict(current["style_controls"] or {}),
                    **changes["style_controls"],
                }
            row = await ProjectsRepository(connection).compare_and_set(
                user["id"], project_id, version, values, now
            )
            if row is None:
                latest = await ProjectsRepository(connection).safe_current(user["id"], project_id)
                if latest is None:
                    raise ApiError("not_found", 404)
                raise ApiError(
                    "revision_conflict",
                    412,
                    context={
                        "current_row_version": latest["row_version"],
                        "current_project": {
                            "id": str(latest["id"]),
                            "name": latest["name"],
                            "script_code": latest["script_code"],
                            "status": latest["status"],
                            "updated_at": latest["updated_at"].astimezone(UTC).isoformat(),
                            "row_version": latest["row_version"],
                        },
                    },
                )
            return self._dto(row)

    async def request_delete(
        self,
        principal: Principal,
        project_id: UUID,
        *,
        if_match: str | None,
    ) -> None:
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            await self._owned_and_versioned(connection, user["id"], project_id, if_match)
            # Phase 07 owns the deletion saga and all physical purge evidence.
            raise ApiError("capability_unavailable", 503)

    def _encode_cursor_for_test(
        self, owner_id: UUID, updated_at: datetime, project_id: UUID
    ) -> str:
        return self._encode_cursor(owner_id, updated_at, project_id)
