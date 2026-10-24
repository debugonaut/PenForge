"""Owner-scoped SQL for bounded project working state."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import project_workspaces, projects


class WorkspacesRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def project(self, owner_id: UUID, project_id: UUID) -> sa.RowMapping | None:
        result = await self.connection.execute(
            sa.select(
                projects.c.id,
                projects.c.name,
                projects.c.script_code,
                projects.c.status,
                projects.c.row_version,
                projects.c.active_snapshot_id,
            ).where(
                projects.c.owner_user_id == owner_id,
                projects.c.id == project_id,
            )
        )
        return result.mappings().one_or_none()

    async def get(self, owner_id: UUID, project_id: UUID) -> sa.RowMapping | None:
        result = await self.connection.execute(
            sa.select(project_workspaces).where(
                project_workspaces.c.owner_user_id == owner_id,
                project_workspaces.c.project_id == project_id,
            )
        )
        return result.mappings().one_or_none()

    async def insert(
        self,
        *,
        owner_id: UUID,
        project_id: UUID,
        workspace: dict[str, Any],
        now: datetime,
    ) -> sa.RowMapping | None:
        result = await self.connection.execute(
            insert(project_workspaces)
            .values(
                project_id=project_id,
                owner_user_id=owner_id,
                workspace=workspace,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["project_id"])
            .returning(project_workspaces)
        )
        return result.mappings().one_or_none()

    async def compare_and_set(
        self,
        *,
        owner_id: UUID,
        project_id: UUID,
        version: int,
        workspace: dict[str, Any],
        now: datetime,
    ) -> sa.RowMapping | None:
        result = await self.connection.execute(
            sa.update(project_workspaces)
            .where(
                project_workspaces.c.owner_user_id == owner_id,
                project_workspaces.c.project_id == project_id,
                project_workspaces.c.row_version == version,
            )
            .values(
                workspace=workspace,
                row_version=version + 1,
                updated_at=now,
            )
            .returning(project_workspaces)
        )
        return result.mappings().one_or_none()
