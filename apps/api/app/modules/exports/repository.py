"""Owner-scoped SQL and the shared PostgreSQL durable-job lease protocol."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import ApiError
from app.db import models as m


class ExportsRepository:
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
            m.jobs.c.job_type == "compile_export",
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
                        m.jobs.c.job_type == "compile_export",
                        m.jobs.c.payload_schema_version == 1,
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
                        m.jobs.c.job_type == "compile_export",
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
                        m.jobs.c.job_type == "compile_export",
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
                    m.jobs.c.job_type == "compile_export",
                    m.jobs.c.state.in_(["queued", "running", "cancel_requested"]),
                )
            )
            or 0
        )

    async def export(self, owner, export_id):
        return (
            (
                await self.connection.execute(
                    sa.select(m.font_exports)
                    .join(m.projects, m.projects.c.id == m.font_exports.c.project_id)
                    .where(m.projects.c.owner_user_id == owner, m.font_exports.c.id == export_id)
                    .with_for_update(of=m.font_exports)
                )
            )
            .mappings()
            .one_or_none()
        )

    async def snapshot(self, owner, project, snapshot):
        return (
            (
                await self.connection.execute(
                    sa.select(m.project_snapshots)
                    .join(m.projects, m.projects.c.id == m.project_snapshots.c.project_id)
                    .where(
                        m.projects.c.owner_user_id == owner,
                        m.project_snapshots.c.project_id == project,
                        m.project_snapshots.c.id == snapshot,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def revision(self, owner, project, glyph, revision):
        return (
            (
                await self.connection.execute(
                    sa.select(m.glyph_revisions, m.glyphs.c.codepoints, m.glyphs.c.variant_index)
                    .join(m.glyphs, m.glyphs.c.id == m.glyph_revisions.c.glyph_id)
                    .join(m.projects, m.projects.c.id == m.glyphs.c.project_id)
                    .where(
                        m.projects.c.owner_user_id == owner,
                        m.glyphs.c.project_id == project,
                        m.glyphs.c.id == glyph,
                        m.glyph_revisions.c.id == revision,
                        m.glyph_revisions.c.approval == "approved",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def insert_export(self, values):
        await self.connection.execute(m.font_exports.insert().values(**values))

    async def update_export(self, owner, export_id, **values):
        row = await self.export(owner, export_id)
        if row is None or row["status"] != "pending":
            return False
        await self.connection.execute(
            m.font_exports.update()
            .where(m.font_exports.c.id == export_id, m.font_exports.c.status == "pending")
            .values(**values)
        )
        return True

    async def insert_asset(self, values):
        await self.connection.execute(m.assets.insert().values(**values))

    async def update_asset(self, owner, aid, **values):
        await self.connection.execute(
            m.assets.update()
            .where(m.assets.c.id == aid, m.assets.c.owner_user_id == owner)
            .values(**values)
        )

    async def reserve(self, owner, job, amounts, now):
        from app.modules.jobs.service import reserve_resources

        await reserve_resources(self.connection, owner, job, amounts, now)

    async def settle_job(self, owner, job, actual):
        from app.modules.jobs.service import settle

        await settle(self.connection, {"id": job, "owner_user_id": owner}, actual)

    async def internal_job(self, job_id):
        return (
            (
                await self.connection.execute(
                    sa.select(m.jobs).where(
                        m.jobs.c.id == job_id, m.jobs.c.job_type == "compile_export"
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def candidate_origin(self, owner, project, candidate):
        return await self.connection.scalar(
            sa.select(m.glyph_candidates.c.origin)
            .join(m.projects, m.projects.c.id == m.glyph_candidates.c.project_id)
            .where(
                m.projects.c.owner_user_id == owner,
                m.glyph_candidates.c.project_id == project,
                m.glyph_candidates.c.id == candidate,
            )
        )

    async def source_hash(self, owner, project, sample):
        row = await self.sample(owner, project, sample)
        return "sha256:" + row["source_sha256"].hex() if row and row["source_sha256"] else None
