"""Working-state rules: owner scope, strong ETags and immutable draft boundaries."""

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.adapters.auth.base import Principal
from app.core.errors import ApiError
from app.modules.identity.service import IdentityService
from app.modules.workspaces.repository import WorkspacesRepository
from app.modules.workspaces.schemas import WorkspaceDocument


class WorkspacesService:
    def __init__(
        self,
        engine: AsyncEngine,
        identity_service: IdentityService,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.engine = engine
        self.identity_service = identity_service
        self.clock = clock

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
        match = re.fullmatch(r'"v([0-9]{1,9})"', if_match)
        if match is None:
            raise ApiError("revision_conflict", 412)
        return int(match.group(1))

    @staticmethod
    def _dto(row: Any) -> dict[str, Any]:
        return {
            "project_id": str(row["project_id"]),
            "workspace": dict(row["workspace"]),
            "row_version": row["row_version"],
            "updated_at": row["updated_at"].astimezone(UTC),
        }

    async def _identity(self, connection: AsyncConnection, principal: Principal) -> Any:
        return await self.identity_service.resolve_in_transaction(
            connection, principal, self.clock()
        )

    async def _owned_project(
        self, connection: AsyncConnection, owner_id: UUID, project_id: UUID
    ) -> Any:
        project = await WorkspacesRepository(connection).project(owner_id, project_id)
        if project is None:
            raise ApiError("not_found", 404)
        if project["status"] == "deleting":
            raise ApiError("state_conflict", 409)
        return project

    async def get(self, principal: Principal, project_id: UUID) -> dict[str, Any]:
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            await self._owned_project(connection, user["id"], project_id)
            row = await WorkspacesRepository(connection).get(user["id"], project_id)
            if row is None:
                # Absent working state is a precondition, not a hidden empty
                # document: the client must create it with If-Match "v0".
                raise ApiError("not_found", 404)
            return self._dto(row)

    async def put(
        self,
        principal: Principal,
        project_id: UUID,
        *,
        if_match: str | None,
        workspace: WorkspaceDocument,
    ) -> dict[str, Any]:
        version = self._version(if_match)
        now = self.clock()
        payload = workspace.model_dump(mode="json")
        async with self._transaction() as connection:
            user = await self._identity(connection, principal)
            await self._owned_project(connection, user["id"], project_id)
            repo = WorkspacesRepository(connection)
            if version == 0:
                created = await repo.insert(
                    owner_id=user["id"],
                    project_id=project_id,
                    workspace=payload,
                    now=now,
                )
                if created is None:
                    raise ApiError(
                        "revision_conflict",
                        412,
                        context={"current_row_version": 1},
                    )
                return self._dto(created)
            updated = await repo.compare_and_set(
                owner_id=user["id"],
                project_id=project_id,
                version=version,
                workspace=payload,
                now=now,
            )
            if updated is None:
                current = await repo.get(user["id"], project_id)
                if current is None:
                    raise ApiError("state_conflict", 409, context={"current_row_version": 0})
                raise ApiError(
                    "revision_conflict",
                    412,
                    context={
                        "current_row_version": current["row_version"],
                        "updated_at": current["updated_at"].astimezone(UTC).isoformat(),
                    },
                )
            return self._dto(updated)
