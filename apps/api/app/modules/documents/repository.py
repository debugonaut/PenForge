"""Owner-scoped SQL for Writing Desk documents and idempotent creation."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from app.db.models import idempotency_keys, writing_documents
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection


class DocumentsRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def get(self, owner_id: UUID, document_id: UUID) -> sa.RowMapping | None:
        result = await self.connection.execute(
            sa.select(writing_documents).where(
                writing_documents.c.owner_user_id == owner_id,
                writing_documents.c.id == document_id,
            )
        )
        return result.mappings().one_or_none()

    async def list_page(
        self,
        owner_id: UUID,
        limit: int,
        before: tuple[datetime, UUID] | None,
    ) -> list[sa.RowMapping]:
        query = sa.select(writing_documents).where(writing_documents.c.owner_user_id == owner_id)
        if before is not None:
            updated_at, document_id = before
            query = query.where(
                sa.or_(
                    writing_documents.c.updated_at < updated_at,
                    sa.and_(
                        writing_documents.c.updated_at == updated_at,
                        writing_documents.c.id < document_id,
                    ),
                )
            )
        result = await self.connection.execute(
            query.order_by(
                writing_documents.c.updated_at.desc(), writing_documents.c.id.desc()
            ).limit(limit + 1)
        )
        return list(result.mappings())

    async def create(
        self,
        *,
        document_id: UUID,
        owner_id: UUID,
        title: str,
        format_: str,
        document: dict[str, Any],
        now: datetime,
    ) -> sa.RowMapping:
        result = await self.connection.execute(
            sa.insert(writing_documents)
            .values(
                id=document_id,
                owner_user_id=owner_id,
                title=title,
                format=format_,
                document=document,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
            .returning(writing_documents)
        )
        return result.mappings().one()

    async def compare_and_set(
        self,
        owner_id: UUID,
        document_id: UUID,
        version: int,
        values: dict[str, Any],
        now: datetime,
    ) -> sa.RowMapping | None:
        result = await self.connection.execute(
            sa.update(writing_documents)
            .where(
                writing_documents.c.owner_user_id == owner_id,
                writing_documents.c.id == document_id,
                writing_documents.c.row_version == version,
            )
            .values(**values, row_version=version + 1, updated_at=now)
            .returning(writing_documents)
        )
        return result.mappings().one_or_none()

    async def delete(self, owner_id: UUID, document_id: UUID, version: int) -> bool:
        result = await self.connection.execute(
            sa.delete(writing_documents).where(
                writing_documents.c.owner_user_id == owner_id,
                writing_documents.c.id == document_id,
                writing_documents.c.row_version == version,
            )
        )
        return bool(result.rowcount)

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

    async def complete_idempotency(
        self,
        owner_id: UUID,
        key: str,
        status: int,
        body: dict[str, Any],
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
