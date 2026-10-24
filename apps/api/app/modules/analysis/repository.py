"""Owner-scoped SQL and the shared PostgreSQL durable-job lease protocol."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import ApiError
from app.db import models as m


class AnalysisRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def now(self) -> datetime:
        value = await self.connection.scalar(sa.select(sa.func.clock_timestamp()))
        assert isinstance(value, datetime)
        return value

    async def user(self, owner: UUID) -> Any:
        return (
            (
                await self.connection.execute(
                    sa.select(m.users).where(m.users.c.id == owner).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

    async def project(self, owner: UUID, project: UUID) -> Any:
        return (
            (
                await self.connection.execute(
                    sa.select(m.projects)
                    .where(
                        m.projects.c.id == project,
                        m.projects.c.owner_user_id == owner,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

    async def sample(self, owner: UUID, project: UUID, sample: UUID) -> Any:
        return (
            (
                await self.connection.execute(
                    sa.select(m.samples)
                    .join(
                        m.projects,
                        m.projects.c.id == m.samples.c.project_id,
                    )
                    .where(
                        m.projects.c.owner_user_id == owner,
                        m.samples.c.project_id == project,
                        m.samples.c.id == sample,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def asset(self, owner: UUID, asset: UUID) -> Any:
        return (
            (
                await self.connection.execute(
                    sa.select(m.assets)
                    .where(
                        m.assets.c.owner_user_id == owner,
                        m.assets.c.id == asset,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

    async def job(self, owner: UUID, job: UUID, *, queued: bool = False) -> Any:
        query = sa.select(m.jobs).where(
            m.jobs.c.id == job,
            m.jobs.c.owner_user_id == owner,
            m.jobs.c.job_type == "analyze_samples",
        )
        if queued:
            query = query.where(
                m.jobs.c.state == "queued", m.jobs.c.run_after <= sa.func.clock_timestamp()
            )
        return (
            (await self.connection.execute(query.with_for_update(skip_locked=queued)))
            .mappings()
            .one_or_none()
        )

    async def candidates(self, worker_mem_mb: int) -> list[Any]:
        return list(
            (
                await self.connection.execute(
                    sa.select(m.jobs)
                    .where(
                        m.jobs.c.job_type == "analyze_samples",
                        m.jobs.c.payload_schema_version.in_([1, 2]),
                        m.jobs.c.state == "queued",
                        m.jobs.c.run_after <= sa.func.clock_timestamp(),
                        sa.cast(
                            sa.func.coalesce(m.jobs.c.resource_profile["mem_mb"].astext, "0"),
                            sa.Integer,
                        )
                        <= worker_mem_mb,
                    )
                    .order_by(m.jobs.c.priority.desc(), m.jobs.c.created_at, m.jobs.c.id)
                    .limit(32)
                )
            ).mappings()
        )

    async def expired(self) -> list[Any]:
        return list(
            (
                await self.connection.execute(
                    sa.select(m.jobs).where(
                        m.jobs.c.job_type == "analyze_samples",
                        m.jobs.c.state.in_(["running", "cancel_requested"]),
                        m.jobs.c.lease_expires_at <= sa.func.clock_timestamp(),
                    )
                )
            ).mappings()
        )

    async def insert_job(self, owner: UUID, values: dict) -> Any:
        assert values["owner_user_id"] == owner
        return (
            (await self.connection.execute(m.jobs.insert().values(**values).returning(m.jobs)))
            .mappings()
            .one()
        )

    async def update_job(self, owner: UUID, job: UUID, **values: Any) -> Any:
        return (
            (
                await self.connection.execute(
                    m.jobs.update()
                    .where(
                        m.jobs.c.id == job,
                        m.jobs.c.owner_user_id == owner,
                        m.jobs.c.job_type == "analyze_samples",
                    )
                    .values(**values)
                    .returning(m.jobs)
                )
            )
            .mappings()
            .one()
        )

    async def event(self, job: UUID, now: datetime, **values: Any) -> None:
        await self.connection.execute(
            m.job_events.insert().values(
                job_id=job,
                created_at=now,
                kind=values.pop("kind", "state"),
                **values,
            )
        )

    async def receipt(
        self, owner: UUID, key: str, digest: bytes, now: datetime, expires: datetime
    ) -> dict | None:
        await self.connection.execute(
            m.idempotency_keys.delete().where(
                m.idempotency_keys.c.user_id == owner,
                m.idempotency_keys.c.key == key,
                m.idempotency_keys.c.expires_at <= now,
            )
        )
        created = await self.connection.scalar(
            insert(m.idempotency_keys)
            .values(
                user_id=owner,
                key=key,
                request_hash=digest,
                state="in_progress",
                created_at=now,
                expires_at=expires,
            )
            .on_conflict_do_nothing()
            .returning(m.idempotency_keys.c.user_id)
        )
        if created is not None:
            return None
        row = (
            (
                await self.connection.execute(
                    sa.select(m.idempotency_keys)
                    .where(
                        m.idempotency_keys.c.user_id == owner,
                        m.idempotency_keys.c.key == key,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if row["request_hash"] != digest:
            raise ApiError("idempotency_key_reuse", 422)
        if row["state"] != "completed":
            raise ApiError("idempotency_in_progress", 409)
        return dict(row["response_body"])

    async def save_receipt(self, owner: UUID, key: str, response: dict) -> None:
        await self.connection.execute(
            m.idempotency_keys.update()
            .where(
                m.idempotency_keys.c.user_id == owner,
                m.idempotency_keys.c.key == key,
            )
            .values(state="completed", response_status=202, response_body=response)
        )

    async def running_count(self, owner: UUID) -> int:
        return int(
            await self.connection.scalar(
                sa.select(sa.func.count())
                .select_from(m.jobs)
                .where(
                    m.jobs.c.owner_user_id == owner,
                    m.jobs.c.job_type == "analyze_samples",
                    m.jobs.c.state.in_(["queued", "running", "cancel_requested"]),
                )
            )
            or 0
        )

    async def update_sample(self, owner: UUID, project: UUID, sample: UUID, **values: Any) -> None:
        assert await self.sample(owner, project, sample) is not None
        await self.connection.execute(
            m.samples.update().where(m.samples.c.id == sample).values(**values)
        )

    async def glyph(self, owner: UUID, project: UUID, codepoints: list[int]) -> Any:
        assert await self.project(owner, project) is not None
        await self.connection.execute(
            insert(m.glyphs)
            .values(
                id=uuid4(),
                project_id=project,
                codepoints=codepoints,
                variant_index=0,
                coverage_status="needs_review",
            )
            .on_conflict_do_nothing(index_elements=["project_id", "codepoints", "variant_index"])
        )
        return (
            (
                await self.connection.execute(
                    sa.select(m.glyphs)
                    .where(
                        m.glyphs.c.project_id == project,
                        m.glyphs.c.codepoints == codepoints,
                        m.glyphs.c.variant_index == 0,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )

    async def insert_result(self, owner: UUID, project: UUID, values: dict) -> Any:
        assert await self.project(owner, project) is not None
        return (
            (
                await self.connection.execute(
                    m.ml_result_artifacts.insert().values(**values).returning(m.ml_result_artifacts)
                )
            )
            .mappings()
            .one()
        )

    async def insert_candidate(self, owner: UUID, project: UUID, values: dict) -> None:
        assert await self.project(owner, project) is not None
        await self.connection.execute(m.glyph_candidates.insert().values(**values))

    async def result(self, owner: UUID, project: UUID, result: UUID) -> Any:
        return (
            (
                await self.connection.execute(
                    sa.select(m.ml_result_artifacts)
                    .join(
                        m.projects,
                        m.projects.c.id == m.ml_result_artifacts.c.project_id,
                    )
                    .where(
                        m.projects.c.owner_user_id == owner,
                        m.projects.c.id == project,
                        m.ml_result_artifacts.c.id == result,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
