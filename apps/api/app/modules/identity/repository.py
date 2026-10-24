"""Owner-scoped identity SQL. Transactions and authorization belong to the service."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import models as m


class IdentityRepository:
    def __init__(self, connection: AsyncConnection):
        self.connection = connection

    async def exists(self, firebase_uid: str) -> bool:
        return bool(
            await self.connection.scalar(
                sa.select(sa.exists().where(m.users.c.firebase_uid == firebase_uid))
            )
        )

    async def insert_identity(
        self, firebase_uid: str, user_id: UUID, anonymous: bool, now: datetime
    ) -> None:
        await self.connection.execute(
            insert(m.users)
            .values(
                id=user_id,
                firebase_uid=firebase_uid,
                is_anonymous=anonymous,
                tier="free_guest" if anonymous else "free_user",
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_nothing(index_elements=[m.users.c.firebase_uid])
        )

    async def lock_identities(self, firebase_uids: list[str]) -> dict[str, RowMapping]:
        result = await self.connection.execute(
            sa.select(m.users)
            .where(m.users.c.firebase_uid.in_(firebase_uids))
            .order_by(m.users.c.id)
            .with_for_update()
        )
        return {row["firebase_uid"]: row for row in result.mappings()}

    async def touch_identity(self, user: RowMapping, anonymous: bool, now: datetime) -> RowMapping:
        values: dict[str, Any] = {"last_seen_at": now, "updated_at": now}
        if not anonymous and user["is_anonymous"]:
            values["is_anonymous"] = False
            if user["tier"] == "free_guest":
                values["tier"] = "free_user"
        result = await self.connection.execute(
            m.users.update().where(m.users.c.id == user["id"]).values(**values).returning(m.users)
        )
        return result.mappings().one()

    async def ensure_guest_usage(
        self, owner_user_id: UUID, now: datetime, ip_hash: bytes | None = None
    ) -> None:
        await self.connection.execute(
            insert(m.guest_usage)
            .values(
                user_id=owner_user_id,
                creations_used=0,
                creations_limit=3,
                first_seen_ip_hash=ip_hash,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=[m.guest_usage.c.user_id])
        )

    async def creations(self, owner_user_id: UUID) -> int:
        sources = sa.select(m.identity_claims.c.guest_user_id).where(
            m.identity_claims.c.destination_user_id == owner_user_id,
            m.identity_claims.c.mode == "transferred",
        )
        return int(
            await self.connection.scalar(
                sa.select(sa.func.coalesce(sa.func.sum(m.guest_usage.c.creations_used), 0)).where(
                    sa.or_(
                        m.guest_usage.c.user_id == owner_user_id,
                        m.guest_usage.c.user_id.in_(sources),
                    )
                )
            )
            or 0
        )

    async def creation_limit(self, owner_user_id: UUID) -> int:
        limit = await self.connection.scalar(
            sa.select(m.guest_usage.c.creations_limit).where(
                m.guest_usage.c.user_id == owner_user_id
            )
        )
        return int(limit) if limit is not None else 3

    async def quota_summary(self, owner_user_id: UUID, now: datetime) -> dict[str, Any]:
        rows = (
            await self.connection.execute(
                sa.select(m.quota_balances)
                .where(
                    m.quota_balances.c.scope == "user",
                    m.quota_balances.c.user_id == owner_user_id,
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
                .order_by(
                    m.quota_balances.c.resource,
                    m.quota_balances.c.window_kind,
                    m.quota_balances.c.window_start,
                )
            )
        ).mappings()
        balances = []
        for row in rows:
            remaining = row["limit_amount"] - row["committed"] - row["reserved"]
            balances.append(
                {
                    key: row[key]
                    for key in (
                        "resource",
                        "window_kind",
                        "window_start",
                        "limit_amount",
                        "committed",
                        "reserved",
                    )
                }
                | {"available": max(0, remaining), "over_limit": remaining < 0}
            )
        return {
            "configured": bool(balances),
            "over_limit": any(item["over_limit"] for item in balances),
            "balances": balances,
        }

    async def receipt(self, owner_user_id: UUID, guest_user_id: UUID) -> dict[str, Any] | None:
        row = (
            await self.connection.execute(
                sa.select(m.identity_claims.c.response_body).where(
                    m.identity_claims.c.guest_user_id == guest_user_id,
                    m.identity_claims.c.destination_user_id == owner_user_id,
                )
            )
        ).scalar_one_or_none()
        return dict(row) if row is not None else None

    async def save_receipt(
        self, owner_user_id: UUID, guest_user_id: UUID, response: dict[str, Any], now: datetime
    ) -> None:
        await self.connection.execute(
            m.identity_claims.insert().values(
                guest_user_id=guest_user_id,
                destination_user_id=owner_user_id,
                mode=response["mode"],
                response_body=response,
                created_at=now,
            )
        )

    async def transfer(
        self, source_user_id: UUID, owner_user_id: UUID, now: datetime
    ) -> dict[str, int]:
        # Preserve ready bytes and references on hash collision. The source object
        # becomes an alias; the unique index covers canonical objects only.
        source_assets = (
            (
                await self.connection.execute(
                    sa.select(m.assets.c.id, m.assets.c.sha256)
                    .where(
                        m.assets.c.owner_user_id == source_user_id,
                        m.assets.c.kind == "original_upload",
                        m.assets.c.state == "ready",
                        m.assets.c.duplicate_of.is_(None),
                    )
                    .order_by(m.assets.c.id)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        for source in source_assets:
            canonical = await self.connection.scalar(
                sa.select(m.assets.c.id)
                .where(
                    m.assets.c.owner_user_id == owner_user_id,
                    m.assets.c.kind == "original_upload",
                    m.assets.c.state == "ready",
                    m.assets.c.sha256 == source["sha256"],
                    m.assets.c.duplicate_of.is_(None),
                )
                .with_for_update()
            )
            if canonical is not None:
                await self.connection.execute(
                    m.assets.update()
                    .where(
                        m.assets.c.owner_user_id == source_user_id,
                        sa.or_(
                            m.assets.c.id == source["id"],
                            m.assets.c.duplicate_of == source["id"],
                        ),
                    )
                    .values(duplicate_of=canonical)
                )

        counts = {}
        for name, table in (("projects", m.projects), ("assets", m.assets), ("jobs", m.jobs)):
            result = await self.connection.execute(
                table.update()
                .where(table.c.owner_user_id == source_user_id)
                .values(owner_user_id=owner_user_id)
                .returning(table.c.id)
            )
            counts[name] = len(result.all())

        # Existing source and target balances are locked in one stable order.
        rows = (
            (
                await self.connection.execute(
                    sa.select(m.quota_balances)
                    .where(m.quota_balances.c.user_id.in_([source_user_id, owner_user_id]))
                    .order_by(m.quota_balances.c.id)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        targets = {
            (r["resource"], r["window_kind"], r["window_start"]): r
            for r in rows
            if r["user_id"] == owner_user_id
        }
        for source in rows:
            if source["user_id"] != source_user_id:
                continue
            key = (source["resource"], source["window_kind"], source["window_start"])
            target = targets.get(key)
            if target is None:
                await self.connection.execute(
                    m.quota_balances.update()
                    .where(m.quota_balances.c.id == source["id"])
                    .values(user_id=owner_user_id, updated_at=now)
                )
            else:
                await self.connection.execute(
                    m.quota_balances.update()
                    .where(m.quota_balances.c.id == target["id"])
                    .values(
                        committed=m.quota_balances.c.committed + source["committed"],
                        reserved=m.quota_balances.c.reserved + source["reserved"],
                        updated_at=now,
                    )
                )
                await self.connection.execute(
                    m.quota_reservations.update()
                    .where(m.quota_reservations.c.balance_id == source["id"])
                    .values(balance_id=target["id"], user_id=owner_user_id)
                )
                await self.connection.execute(
                    m.quota_balances.delete().where(m.quota_balances.c.id == source["id"])
                )
        # Include global-balance reservations. Their balances must never be added twice.
        result = await self.connection.execute(
            m.quota_reservations.update()
            .where(m.quota_reservations.c.user_id == source_user_id)
            .values(user_id=owner_user_id)
            .returning(m.quota_reservations.c.id)
        )
        # Repointed user-balance reservations were already changed above.
        counts["reservations"] = len(result.all())
        # Derive exact source reservation count from jobs transferred or historical source.
        return counts

    async def reservation_count(self, owner_user_id: UUID) -> int:
        return int(
            await self.connection.scalar(
                sa.select(sa.func.count())
                .select_from(m.quota_reservations)
                .where(m.quota_reservations.c.user_id == owner_user_id)
            )
            or 0
        )

    async def withdraw_guest_contributions(self, source_user_id: UUID, now: datetime) -> None:
        consents = sa.select(m.training_consents.c.id).where(
            m.training_consents.c.user_id == source_user_id
        )
        await self.connection.execute(
            m.dataset_contributions.update()
            .where(
                m.dataset_contributions.c.consent_id.in_(consents),
                m.dataset_contributions.c.status == "active",
            )
            .values(status="withdrawn", withdrawn_at=now)
        )
        await self.connection.execute(
            m.training_consents.update()
            .where(
                m.training_consents.c.user_id == source_user_id,
                m.training_consents.c.withdrawn_at.is_(None),
            )
            .values(withdrawn_at=now)
        )

    async def mark_merged(self, source_user_id: UUID, owner_user_id: UUID, now: datetime) -> None:
        await self.connection.execute(
            m.users.update()
            .where(m.users.c.id == source_user_id)
            .values(merged_into_user_id=owner_user_id, updated_at=now)
        )

    async def audit_claim(
        self,
        owner_user_id: UUID,
        source_user_id: UUID,
        counts: dict[str, int],
        request_id: str | None,
        now: datetime,
    ) -> None:
        await self.connection.execute(
            m.audit_events.insert().values(
                occurred_at=now,
                actor_ref=str(owner_user_id),
                action="identity.claim",
                resource_type="identity",
                resource_id=source_user_id,
                outcome="success",
                request_id=request_id,
                metadata=counts,
            )
        )

    async def project(self, owner_user_id: UUID, project_id: UUID) -> RowMapping | None:
        return (
            (
                await self.connection.execute(
                    sa.select(m.projects).where(
                        m.projects.c.id == project_id, m.projects.c.owner_user_id == owner_user_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def asset(self, owner_user_id: UUID, asset_id: UUID) -> RowMapping | None:
        return (
            (
                await self.connection.execute(
                    sa.select(m.assets).where(
                        m.assets.c.id == asset_id, m.assets.c.owner_user_id == owner_user_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    async def purge_inventory(
        self, owner_user_id: UUID, project_id: UUID | None = None
    ) -> list[RowMapping]:
        """Internal deletion inventory, never a wire DTO; historical prefixes are inert."""
        query = sa.select(m.assets.c.id, m.assets.c.storage_backend, m.assets.c.storage_key).where(
            m.assets.c.owner_user_id == owner_user_id, m.assets.c.state != "deleted"
        )
        if project_id is not None:
            query = query.where(m.assets.c.project_id == project_id)
        return list((await self.connection.execute(query.order_by(m.assets.c.id))).mappings())
