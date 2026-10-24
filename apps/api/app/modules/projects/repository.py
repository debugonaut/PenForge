"""Owner-scoped SQL for project metadata and idempotent project creation."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import guest_usage, idempotency_keys, projects


class ProjectsRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def get(self, owner_id: UUID, project_id: UUID) -> sa.RowMapping | None:
        result = await self.connection.execute(
            sa.select(projects).where(
                projects.c.owner_user_id == owner_id,
                projects.c.id == project_id,
            )
        )
        return result.mappings().one_or_none()

    async def list_page(
        self,
        owner_id: UUID,
        limit: int,
        before: tuple[datetime, UUID] | None,
    ) -> list[sa.RowMapping]:
        query = sa.select(projects).where(projects.c.owner_user_id == owner_id)
        if before is not None:
            updated_at, project_id = before
            query = query.where(
                sa.or_(
                    projects.c.updated_at < updated_at,
                    sa.and_(projects.c.updated_at == updated_at, projects.c.id < project_id),
                )
            )
        result = await self.connection.execute(
            query.order_by(projects.c.updated_at.desc(), projects.c.id.desc()).limit(limit + 1)
        )
        return list(result.mappings())

    async def claim_idempotency(
        self,
        owner_id: UUID,
        key: str,
        request_hash: bytes,
        now: datetime,
        expires_at: datetime,
    ) -> tuple[bool, sa.RowMapping | None]:
        await self.connection.execute(
            sa.delete(idempotency_keys).where(
                idempotency_keys.c.user_id == owner_id,
                idempotency_keys.c.key == key,
                idempotency_keys.c.expires_at <= now,
            )
        )
        statement = (
            insert(idempotency_keys)
            .values(
                user_id=owner_id,
                key=key,
                request_hash=request_hash,
                state="in_progress",
                response_status=None,
                response_body=None,
                created_at=now,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "key"])
            .returning(idempotency_keys.c.user_id)
        )
        result = await self.connection.execute(statement)
        if result.scalar_one_or_none() is not None:
            # The record is completed before this transaction can commit. A
            # concurrent insertion waits on the primary key and then replays.
            return True, None
        existing = await self.connection.execute(
            sa.select(idempotency_keys)
            .where(
                idempotency_keys.c.user_id == owner_id,
                idempotency_keys.c.key == key,
            )
            .with_for_update()
        )
        return False, existing.mappings().one_or_none()

    async def reap_expired_idempotency(self, now: datetime, batch_size: int = 100) -> int:
        result = await self.connection.execute(
            sa.text(
                """WITH expired AS (
                     SELECT user_id, key
                       FROM idempotency_keys
                      WHERE expires_at <= :now
                      ORDER BY expires_at
                      LIMIT :batch_size
                      FOR UPDATE SKIP LOCKED
                   )
                   DELETE FROM idempotency_keys AS keys
                    USING expired
                    WHERE keys.user_id = expired.user_id AND keys.key = expired.key
                   RETURNING keys.key"""
            ),
            {"now": now, "batch_size": batch_size},
        )
        return len(result.fetchall())

    async def complete_idempotency(
        self,
        owner_id: UUID,
        key: str,
        status: int,
        body: dict[str, Any],
        now: datetime,
        expires_at: datetime,
    ) -> None:
        await self.connection.execute(
            sa.update(idempotency_keys)
            .where(
                idempotency_keys.c.user_id == owner_id,
                idempotency_keys.c.key == key,
            )
            .values(
                state="completed",
                response_status=status,
                response_body=body,
                expires_at=expires_at,
            )
        )

    async def consume_guest_creation(self, owner_id: UUID, now: datetime) -> bool:
        result = await self.connection.execute(
            sa.update(guest_usage)
            .where(
                guest_usage.c.user_id == owner_id,
                guest_usage.c.creations_used < guest_usage.c.creations_limit,
            )
            .values(creations_used=guest_usage.c.creations_used + 1, updated_at=now)
            .returning(guest_usage.c.creations_used)
        )
        return result.scalar_one_or_none() is not None

    async def create(
        self,
        *,
        project_id: UUID,
        owner_id: UUID,
        name: str,
        script_code: str,
        style_controls: dict[str, float],
        now: datetime,
    ) -> sa.RowMapping:
        result = await self.connection.execute(
            sa.insert(projects)
            .values(
                id=project_id,
                owner_user_id=owner_id,
                name=name,
                script_code=script_code,
                status="draft",
                active_snapshot_id=None,
                style_controls=style_controls,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            .returning(projects)
        )
        return result.mappings().one()

    async def compare_and_set(
        self,
        owner_id: UUID,
        project_id: UUID,
        version: int,
        values: dict[str, Any],
        now: datetime,
    ) -> sa.RowMapping | None:
        result = await self.connection.execute(
            sa.update(projects)
            .where(
                projects.c.owner_user_id == owner_id,
                projects.c.id == project_id,
                projects.c.row_version == version,
                projects.c.status != "deleting",
            )
            .values(**values, row_version=version + 1, updated_at=now)
            .returning(projects)
        )
        return result.mappings().one_or_none()

    async def safe_current(self, owner_id: UUID, project_id: UUID) -> sa.RowMapping | None:
        result = await self.connection.execute(
            sa.select(
                projects.c.id,
                projects.c.name,
                projects.c.script_code,
                projects.c.status,
                projects.c.updated_at,
                projects.c.row_version,
            ).where(projects.c.owner_user_id == owner_id, projects.c.id == project_id)
        )
        return result.mappings().one_or_none()
