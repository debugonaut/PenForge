"""Owner-scoped review SQL; services own the transactions and approval decisions."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import models as m


class GlyphsRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def project(self, owner: UUID, project: UUID, *, lock: bool = False):
        query = sa.select(m.projects).where(
            m.projects.c.id == project, m.projects.c.owner_user_id == owner
        )
        if lock:
            query = query.with_for_update()
        return (await self.connection.execute(query)).mappings().one_or_none()

    async def glyph(self, owner: UUID, project: UUID, glyph: UUID, *, lock: bool = False):
        query = (
            sa.select(m.glyphs)
            .join(m.projects)
            .where(
                m.projects.c.owner_user_id == owner,
                m.glyphs.c.project_id == project,
                m.glyphs.c.id == glyph,
            )
        )
        if lock:
            query = query.with_for_update(of=m.glyphs)
        return (await self.connection.execute(query)).mappings().one_or_none()

    async def glyphs(
        self, owner: UUID, project: UUID, *, limit: int = 100, cursor: UUID | None = None
    ):
        query = (
            sa.select(m.glyphs)
            .join(m.projects)
            .where(m.projects.c.owner_user_id == owner, m.glyphs.c.project_id == project)
        )
        if cursor:
            query = query.where(m.glyphs.c.id > cursor)
        return list(
            (await self.connection.execute(query.order_by(m.glyphs.c.id).limit(limit))).mappings()
        )

    async def revisions(self, owner: UUID, project: UUID, glyph: UUID):
        query = (
            sa.select(m.glyph_revisions)
            .select_from(m.glyph_revisions)
            .join(m.glyphs, m.glyphs.c.id == m.glyph_revisions.c.glyph_id)
            .join(m.projects, m.projects.c.id == m.glyphs.c.project_id)
            .where(
                m.projects.c.owner_user_id == owner,
                m.glyphs.c.project_id == project,
                m.glyphs.c.id == glyph,
            )
            .order_by(m.glyph_revisions.c.seq.desc())
        )
        return list((await self.connection.execute(query)).mappings())

    async def candidates(self, owner: UUID, project: UUID, glyph: UUID):
        query = (
            sa.select(m.glyph_candidates)
            .join(m.projects, m.projects.c.id == m.glyph_candidates.c.project_id)
            .where(
                m.projects.c.owner_user_id == owner,
                m.glyph_candidates.c.project_id == project,
                m.glyph_candidates.c.glyph_id == glyph,
            )
            .order_by(m.glyph_candidates.c.rank, m.glyph_candidates.c.id)
        )
        return list((await self.connection.execute(query)).mappings())

    async def asset(self, owner: UUID, project: UUID, asset: UUID):
        return (
            (
                await self.connection.execute(
                    sa.select(m.assets).where(
                        m.assets.c.owner_user_id == owner,
                        m.assets.c.project_id == project,
                        m.assets.c.id == asset,
                        m.assets.c.state == "ready",
                        m.assets.c.kind == "vector",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def insert_revision(self, values: dict[str, Any]):
        return (
            (
                await self.connection.execute(
                    m.glyph_revisions.insert().values(**values).returning(m.glyph_revisions)
                )
            )
            .mappings()
            .one()
        )

    async def update_revision(self, glyph: UUID, revision: UUID, **values):
        await self.connection.execute(
            m.glyph_revisions.update()
            .where(m.glyph_revisions.c.glyph_id == glyph, m.glyph_revisions.c.id == revision)
            .values(**values)
        )

    async def update_glyph(self, project: UUID, glyph: UUID, version: int, **values):
        result = await self.connection.execute(
            m.glyphs.update()
            .where(
                m.glyphs.c.project_id == project,
                m.glyphs.c.id == glyph,
                m.glyphs.c.row_version == version,
            )
            .values(**values, row_version=version + 1)
            .returning(m.glyphs)
        )
        return result.mappings().one_or_none()

    async def review_candidate(self, project: UUID, candidate: UUID, status: str):
        await self.connection.execute(
            m.glyph_candidates.update()
            .where(
                m.glyph_candidates.c.project_id == project,
                m.glyph_candidates.c.id == candidate,
                m.glyph_candidates.c.review_status == "pending",
            )
            .values(review_status=status)
        )

    async def ensure_space(self, project: UUID, glyph: UUID, now: datetime):
        await self.connection.execute(
            insert(m.glyphs)
            .values(id=glyph, project_id=project, codepoints=[32], created_at=now, updated_at=now)
            .on_conflict_do_nothing(index_elements=["project_id", "codepoints", "variant_index"])
        )
        return (
            (
                await self.connection.execute(
                    sa.select(m.glyphs)
                    .where(
                        m.glyphs.c.project_id == project,
                        m.glyphs.c.codepoints == [32],
                        m.glyphs.c.variant_index == 0,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )

    async def claim_key(
        self, owner: UUID, key: str, digest: bytes, now: datetime, expires: datetime
    ):
        await self.connection.execute(
            m.idempotency_keys.delete().where(
                m.idempotency_keys.c.user_id == owner,
                m.idempotency_keys.c.key == key,
                m.idempotency_keys.c.expires_at <= now,
            )
        )
        row = await self.connection.execute(
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
            .returning(m.idempotency_keys.c.key)
        )
        if row.scalar_one_or_none():
            return None
        return (
            (
                await self.connection.execute(
                    sa.select(m.idempotency_keys)
                    .where(m.idempotency_keys.c.user_id == owner, m.idempotency_keys.c.key == key)
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )

    async def complete_key(self, owner: UUID, key: str, body: dict[str, Any], status: int):
        await self.connection.execute(
            m.idempotency_keys.update()
            .where(m.idempotency_keys.c.user_id == owner, m.idempotency_keys.c.key == key)
            .values(state="completed", response_status=status, response_body=body)
        )

    async def snapshot(self, owner: UUID, project: UUID, snapshot: UUID):
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

    async def insert_snapshot(self, values: dict[str, Any]):
        return (
            (
                await self.connection.execute(
                    m.project_snapshots.insert().values(**values).returning(m.project_snapshots)
                )
            )
            .mappings()
            .one()
        )

    async def snapshot_references(self, owner: UUID, project: UUID):
        query = (
            sa.select(
                m.glyphs.c.id, m.glyphs.c.active_revision_id, m.glyph_revisions.c.model_version_id
            )
            .select_from(m.glyphs)
            .join(m.projects, m.projects.c.id == m.glyphs.c.project_id)
            .join(m.glyph_revisions, m.glyph_revisions.c.id == m.glyphs.c.active_revision_id)
            .where(
                m.projects.c.owner_user_id == owner,
                m.glyphs.c.project_id == project,
                m.glyph_revisions.c.approval == "approved",
                m.glyphs.c.coverage_status != "excluded",
            )
            .order_by(m.glyphs.c.id)
        )
        return list((await self.connection.execute(query)).mappings())

    async def style_profile(self, project: UUID):
        return (
            (
                await self.connection.execute(
                    sa.select(m.style_profiles)
                    .where(m.style_profiles.c.project_id == project)
                    .order_by(m.style_profiles.c.version.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )

    async def insert_style(self, values):
        return (
            (
                await self.connection.execute(
                    m.style_profiles.insert().values(**values).returning(m.style_profiles)
                )
            )
            .mappings()
            .one()
        )

    async def profile_identity(self, project, digest):
        return (
            (
                await self.connection.execute(
                    sa.select(m.style_profiles).where(
                        m.style_profiles.c.project_id == project,
                        m.style_profiles.c.profile_sha256 == digest,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def profile_id(self, owner, profile_id):
        return (
            (
                await self.connection.execute(
                    sa.select(m.style_profiles)
                    .join(m.projects)
                    .where(m.projects.c.owner_user_id == owner, m.style_profiles.c.id == profile_id)
                )
            )
            .mappings()
            .one_or_none()
        )

    async def insert_glyph(self, values):
        return (
            (await self.connection.execute(m.glyphs.insert().values(**values).returning(m.glyphs)))
            .mappings()
            .one()
        )

    async def sample_hash(self, owner, project, sample_id):
        return await self.connection.scalar(
            sa.select(m.samples.c.source_sha256)
            .join(m.projects)
            .where(
                m.projects.c.owner_user_id == owner,
                m.samples.c.project_id == project,
                m.samples.c.id == sample_id,
            )
        )

    async def profile_source(self, owner, project, sample_id):
        return (
            (
                await self.connection.execute(
                    sa.select(m.assets)
                    .join(m.samples, m.samples.c.asset_id == m.assets.c.id)
                    .where(
                        m.assets.c.owner_user_id == owner,
                        m.assets.c.project_id == project,
                        m.assets.c.state == "ready",
                        m.samples.c.project_id == project,
                        m.samples.c.id == sample_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def snapshot_coverage_context(self, owner, project):
        query = (
            sa.select(
                m.glyphs.c.codepoints,
                m.glyph_candidates.c.origin,
                m.glyph_candidates.c.review_status,
            )
            .select_from(m.glyph_candidates)
            .join(m.glyphs, m.glyphs.c.id == m.glyph_candidates.c.glyph_id)
            .join(m.projects, m.projects.c.id == m.glyphs.c.project_id)
            .where(m.projects.c.owner_user_id == owner, m.glyphs.c.project_id == project)
        )
        rows = list((await self.connection.execute(query)).mappings())
        pending = sorted(
            {
                tuple(r["codepoints"])
                for r in rows
                if r["origin"] in {"geometric_reconstruction", "style_conditioned_generation"}
                and r["review_status"] == "pending"
            }
        )
        return {
            "generated_pending": [list(cp) for cp in pending],
            "observation": "frozen_at_snapshot",
        }
