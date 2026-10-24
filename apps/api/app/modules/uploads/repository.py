"""Owner-scoped capture SQL. Callers own transactions and lock users first."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import models as m


class UploadsRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def project(self, owner_id: UUID, project_id: UUID) -> sa.RowMapping | None:
        return (
            (
                await self.connection.execute(
                    sa.select(m.projects)
                    .where(m.projects.c.id == project_id, m.projects.c.owner_user_id == owner_id)
                    .with_for_update(read=True)
                )
            )
            .mappings()
            .one_or_none()
        )

    async def asset(self, owner_id: UUID, asset_id: UUID) -> sa.RowMapping | None:
        return (
            (
                await self.connection.execute(
                    sa.select(m.assets)
                    .where(m.assets.c.id == asset_id, m.assets.c.owner_user_id == owner_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

    async def canonical(self, owner_id: UUID, digest: bytes) -> sa.RowMapping | None:
        return (
            (
                await self.connection.execute(
                    sa.select(m.assets)
                    .where(
                        m.assets.c.owner_user_id == owner_id,
                        m.assets.c.sha256 == digest,
                        m.assets.c.kind == "original_upload",
                        m.assets.c.state == "ready",
                        m.assets.c.duplicate_of.is_(None),
                    )
                    .order_by(m.assets.c.id)
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )

    async def asset_count(self, owner_id: UUID, project_id: UUID) -> int:
        return int(
            await self.connection.scalar(
                sa.select(sa.func.count())
                .select_from(m.assets)
                .where(
                    m.assets.c.owner_user_id == owner_id,
                    m.assets.c.project_id == project_id,
                    m.assets.c.kind.in_(["original_upload", "raw_strokes"]),
                    m.assets.c.state.not_in(["deleted", "expired", "rejected"]),
                )
            )
            or 0
        )

    async def insert_asset(self, values: dict[str, Any]) -> sa.RowMapping:
        return (
            (await self.connection.execute(m.assets.insert().values(**values).returning(m.assets)))
            .mappings()
            .one()
        )

    async def update_asset(self, owner_id: UUID, asset_id: UUID, **values: Any) -> sa.RowMapping:
        return (
            (
                await self.connection.execute(
                    m.assets.update()
                    .where(m.assets.c.id == asset_id, m.assets.c.owner_user_id == owner_id)
                    .values(**values)
                    .returning(m.assets)
                )
            )
            .mappings()
            .one()
        )

    async def insert_sample(self, values: dict[str, Any]) -> sa.RowMapping:
        return (
            (
                await self.connection.execute(
                    m.samples.insert().values(**values).returning(m.samples)
                )
            )
            .mappings()
            .one()
        )

    async def samples(self, owner_id: UUID, project_id: UUID) -> list[sa.RowMapping]:
        return list(
            (
                await self.connection.execute(
                    sa.select(m.samples)
                    .join(m.projects, m.projects.c.id == m.samples.c.project_id)
                    .where(
                        m.projects.c.owner_user_id == owner_id, m.samples.c.project_id == project_id
                    )
                    .order_by(m.samples.c.created_at, m.samples.c.id)
                )
            ).mappings()
        )

    async def storage_balances(self, owner_id: UUID, now: datetime) -> list[sa.RowMapping]:
        from app.modules.jobs.service import configure_balances

        await configure_balances(self.connection, owner_id, ["storage_bytes"], now)
        return list(
            (
                await self.connection.execute(
                    sa.select(m.quota_balances)
                    .where(
                        m.quota_balances.c.resource == "storage_bytes",
                        sa.or_(
                            m.quota_balances.c.user_id == owner_id,
                            m.quota_balances.c.scope == "global",
                        ),
                        sa.or_(
                            m.quota_balances.c.window_kind == "lifetime",
                            sa.and_(
                                m.quota_balances.c.window_kind == "day",
                                m.quota_balances.c.window_start == now.date(),
                            ),
                            sa.and_(
                                m.quota_balances.c.window_kind == "month",
                                m.quota_balances.c.window_start == now.date().replace(day=1),
                            ),
                        ),
                    )
                    .order_by(m.quota_balances.c.id)
                    .with_for_update()
                )
            ).mappings()
        )

    async def reserve(
        self,
        owner_id: UUID,
        asset_id: UUID,
        amount: int,
        expires: datetime,
        balances: list[sa.RowMapping],
        ids: Any,
        now: datetime,
    ) -> None:
        for row in balances:
            await self.connection.execute(
                m.quota_balances.update()
                .where(m.quota_balances.c.id == row["id"])
                .values(reserved=m.quota_balances.c.reserved + amount, updated_at=now)
            )
            await self.connection.execute(
                m.quota_reservations.insert().values(
                    id=ids(),
                    balance_id=row["id"],
                    user_id=owner_id,
                    asset_id=asset_id,
                    resource="storage_bytes",
                    amount=amount,
                    status="reserved",
                    expires_at=expires,
                    created_at=now,
                )
            )

    async def settle(
        self,
        owner_id: UUID,
        asset_id: UUID,
        actual: int | None,
        now: datetime,
        *,
        expired: bool = False,
    ) -> None:
        from app.modules.jobs.service import lock_balances

        await lock_balances(self.connection, owner_id)
        # Lock balances before reservations, matching reserve/claim stable ordering.
        balance_ids = sa.select(m.quota_reservations.c.balance_id).where(
            m.quota_reservations.c.asset_id == asset_id,
            m.quota_reservations.c.user_id == owner_id,
            m.quota_reservations.c.status == "reserved",
        )
        await self.connection.execute(
            sa.select(m.quota_balances.c.id)
            .where(m.quota_balances.c.id.in_(balance_ids))
            .order_by(m.quota_balances.c.id)
            .with_for_update()
        )
        reservations = (
            (
                await self.connection.execute(
                    sa.select(m.quota_reservations)
                    .where(
                        m.quota_reservations.c.asset_id == asset_id,
                        m.quota_reservations.c.user_id == owner_id,
                        m.quota_reservations.c.status == "reserved",
                    )
                    .order_by(m.quota_reservations.c.id)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        for row in reservations:
            if actual is not None and not 0 <= actual <= row["amount"]:
                raise ValueError("storage_usage_exceeds_reservation")
            await self.connection.execute(
                m.quota_balances.update()
                .where(m.quota_balances.c.id == row["balance_id"])
                .values(
                    reserved=m.quota_balances.c.reserved - row["amount"],
                    committed=m.quota_balances.c.committed + (actual or 0),
                    updated_at=now,
                )
            )
            await self.connection.execute(
                m.quota_reservations.update()
                .where(
                    m.quota_reservations.c.id == row["id"],
                    m.quota_reservations.c.status == "reserved",
                )
                .values(
                    status="committed"
                    if actual is not None
                    else ("expired" if expired else "released"),
                    committed_amount=actual,
                    settled_at=now,
                )
            )

    async def job(self, owner_id: UUID, job_id: UUID) -> sa.RowMapping | None:
        return (
            (
                await self.connection.execute(
                    sa.select(m.jobs)
                    .where(m.jobs.c.id == job_id, m.jobs.c.owner_user_id == owner_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

    async def insert_job(self, values: dict[str, Any]) -> sa.RowMapping:
        return (
            (await self.connection.execute(m.jobs.insert().values(**values).returning(m.jobs)))
            .mappings()
            .one()
        )

    async def event(
        self, job_id: UUID, now: datetime, *, kind: str = "state", **values: Any
    ) -> None:
        await self.connection.execute(
            m.job_events.insert().values(job_id=job_id, kind=kind, created_at=now, **values)
        )

    async def update_job(self, owner_id: UUID, job_id: UUID, **values: Any) -> sa.RowMapping:
        return (
            (
                await self.connection.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == job_id, m.jobs.c.owner_user_id == owner_id)
                    .values(**values)
                    .returning(m.jobs)
                )
            )
            .mappings()
            .one()
        )

    async def asset_for_capability(self, key: str) -> sa.RowMapping | None:
        # Internal capability resolution is not a user-data lookup: encrypted,
        # verified key + exact issued token fingerprint is required by service.
        return (
            (
                await self.connection.execute(
                    sa.select(m.assets).where(m.assets.c.storage_key == key)
                )
            )
            .mappings()
            .one_or_none()
        )

    async def user(self, owner_id: UUID) -> sa.RowMapping | None:
        return (
            (
                await self.connection.execute(
                    sa.select(m.users).where(m.users.c.id == owner_id).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

    async def now(self) -> datetime:
        return (await self.connection.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()

    async def queue_candidates(
        self, types: tuple[str, ...], versions: tuple[int, ...], memory: int
    ) -> list[sa.RowMapping]:
        running = sa.select(sa.func.count()).select_from(m.jobs.alias("running"))
        del running
        return list(
            (
                await self.connection.execute(
                    sa.select(m.jobs)
                    .where(
                        m.jobs.c.state == "queued",
                        m.jobs.c.run_after <= sa.func.clock_timestamp(),
                        m.jobs.c.job_type.in_(types),
                        m.jobs.c.payload_schema_version.in_(versions),
                        sa.cast(
                            sa.func.coalesce(m.jobs.c.resource_profile["mem_mb"].astext, "0"),
                            sa.Integer,
                        )
                        <= memory,
                    )
                    .order_by(m.jobs.c.priority.desc(), m.jobs.c.created_at, m.jobs.c.id)
                    .limit(32)
                )
            ).mappings()
        )

    async def lock_candidate(self, owner_id: UUID, job_id: UUID) -> sa.RowMapping | None:
        return (
            (
                await self.connection.execute(
                    sa.select(m.jobs)
                    .where(
                        m.jobs.c.id == job_id,
                        m.jobs.c.owner_user_id == owner_id,
                        m.jobs.c.state == "queued",
                        m.jobs.c.run_after <= sa.func.clock_timestamp(),
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .mappings()
            .one_or_none()
        )

    async def expired_jobs(self) -> list[sa.RowMapping]:
        return list(
            (
                await self.connection.execute(
                    sa.select(m.jobs)
                    .where(
                        m.jobs.c.job_type == "asset_verify",
                        m.jobs.c.state.in_(["running", "cancel_requested"]),
                        m.jobs.c.lease_expires_at <= sa.func.clock_timestamp(),
                    )
                    .order_by(m.jobs.c.lease_expires_at)
                    .limit(100)
                )
            ).mappings()
        )

    async def cleanup_candidates(self, now: datetime) -> list[sa.RowMapping]:
        return list(
            (
                await self.connection.execute(
                    sa.select(m.assets)
                    .where(
                        sa.or_(
                            sa.and_(
                                m.assets.c.state == "pending_upload", m.assets.c.expires_at <= now
                            ),
                            sa.and_(
                                m.assets.c.state.in_(["expired", "rejected"]),
                                m.assets.c.cleanup_completed_at.is_(None),
                            ),
                        )
                    )
                    .order_by(m.assets.c.id)
                    .limit(100)
                )
            ).mappings()
        )

    async def inventory(self) -> list[sa.RowMapping]:
        return list(
            (
                await self.connection.execute(
                    sa.select(
                        m.assets.c.id,
                        m.assets.c.storage_key,
                        m.assets.c.state,
                        m.assets.c.cleanup_completed_at,
                    ).where(m.assets.c.state != "deleted")
                )
            ).mappings()
        )

    async def receipt(
        self, owner_id: UUID, key: str, digest: bytes, now: datetime, expires: datetime
    ) -> dict[str, Any] | None:
        from sqlalchemy.dialects.postgresql import insert

        from app.core.errors import ApiError

        await self.connection.execute(
            m.idempotency_keys.delete().where(
                m.idempotency_keys.c.user_id == owner_id,
                m.idempotency_keys.c.key == key,
                m.idempotency_keys.c.expires_at <= now,
            )
        )
        inserted = await self.connection.scalar(
            insert(m.idempotency_keys)
            .values(
                user_id=owner_id,
                key=key,
                request_hash=digest,
                state="in_progress",
                created_at=now,
                expires_at=expires,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "key"])
            .returning(m.idempotency_keys.c.user_id)
        )
        if inserted is not None:
            return None
        row = (
            (
                await self.connection.execute(
                    sa.select(m.idempotency_keys)
                    .where(
                        m.idempotency_keys.c.user_id == owner_id, m.idempotency_keys.c.key == key
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
            raise ApiError("idempotency_in_progress", 409, headers={"Retry-After": "1"})
        return dict(row["response_body"])

    async def save_receipt(
        self, owner_id: UUID, key: str, body: dict[str, Any], status: int
    ) -> None:
        await self.connection.execute(
            m.idempotency_keys.update()
            .where(m.idempotency_keys.c.user_id == owner_id, m.idempotency_keys.c.key == key)
            .values(state="completed", response_status=status, response_body=body)
        )

    async def attach_reservations(self, owner_id: UUID, asset_id: UUID, job_id: UUID) -> None:
        await self.connection.execute(
            m.quota_reservations.update()
            .where(
                m.quota_reservations.c.user_id == owner_id,
                m.quota_reservations.c.asset_id == asset_id,
            )
            .values(job_id=job_id)
        )

    async def asset_by_id_internal(self, asset_id: UUID) -> sa.RowMapping | None:
        # Queue/maintenance discovery only. The service locks/rechecks current owner.
        return (
            (await self.connection.execute(sa.select(m.assets).where(m.assets.c.id == asset_id)))
            .mappings()
            .one_or_none()
        )

    async def finalize_fenced(
        self, owner_id: UUID, job_id: UUID, holder: str, attempt: int, now: datetime
    ) -> bool:
        statement = (
            m.jobs.update()
            .where(
                m.jobs.c.id == job_id,
                m.jobs.c.owner_user_id == owner_id,
                m.jobs.c.state == "running",
                m.jobs.c.lease_holder == holder,
                m.jobs.c.attempts == attempt,
                m.jobs.c.lease_expires_at > sa.func.clock_timestamp(),
                m.jobs.c.cancel_requested.is_(False),
                sa.exists(
                    sa.select(m.users.c.id).where(
                        m.users.c.id == owner_id,
                        m.users.c.account_state == "active",
                        m.users.c.merged_into_user_id.is_(None),
                    )
                ),
                sa.exists(
                    sa.select(m.projects.c.id).where(
                        m.projects.c.id == m.jobs.c.project_id,
                        m.projects.c.owner_user_id == owner_id,
                        m.projects.c.status != "deleting",
                    )
                ),
            )
            .values(
                state="succeeded",
                stage="verified",
                progress=1,
                checkpoint={"completed_units": 4, "total_units": 4},
                finished_at=now,
                lease_holder=None,
                lease_expires_at=None,
                error_code=None,
            )
            .returning(m.jobs.c.id)
        )
        return await self.connection.scalar(statement) is not None

    async def job_by_id_internal(self, job_id: UUID) -> sa.RowMapping | None:
        return (
            (await self.connection.execute(sa.select(m.jobs).where(m.jobs.c.id == job_id)))
            .mappings()
            .one_or_none()
        )

    async def asset_for_job(self, owner_id: UUID, job_id: UUID) -> sa.RowMapping | None:
        return (
            (
                await self.connection.execute(
                    sa.select(m.assets)
                    .where(m.assets.c.owner_user_id == owner_id, m.assets.c.verify_job_id == job_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
