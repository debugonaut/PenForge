"""Verified identity and naturally idempotent ownership claims, one transaction each."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import InterfaceError, OperationalError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.adapters.auth.base import Principal
from app.core.errors import ApiError
from app.modules.identity.repository import IdentityRepository


class IdentityService:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        ids: Callable[[], UUID] = uuid4,
        claim_max_age_seconds: int = 600,
        erasure_check=None,
    ):
        self.engine = engine
        self.clock = clock
        self.ids = ids
        self.claim_max_age_seconds = claim_max_age_seconds
        self.erasure_check = erasure_check

    async def identity_exists(self, firebase_uid: str) -> bool:
        async with self.transaction() as connection:
            return await IdentityRepository(connection).exists(firebase_uid)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """Public transaction seam for account services; infrastructure errors stay private."""
        try:
            async with self.engine.begin() as connection:
                yield connection
        except (OperationalError, InterfaceError, TimeoutError):
            # Connection details, SQL parameters and transaction errors stay private.
            raise ApiError("dependency_unavailable", 503) from None

    @staticmethod
    def _active(user: RowMapping, *, allow_merged: bool = False) -> None:
        if user["account_state"] == "suspended":
            raise ApiError("account_suspended", 403)
        if user["account_state"] == "deleting":
            raise ApiError("account_deleting", 403)
        if user["merged_into_user_id"] is not None and not allow_merged:
            raise ApiError("account_merged", 403)

    async def resolve_identity(
        self,
        repo: IdentityRepository,
        principal: Principal,
        now: datetime,
        first_seen_ip_hash: bytes | None = None,
    ) -> RowMapping:
        import sqlalchemy as sa

        await repo.connection.execute(sa.select(sa.func.pg_advisory_xact_lock(70707001)))
        if self.erasure_check and await self.erasure_check(principal.firebase_uid):
            raise ApiError("account_deleting", 403)
        await repo.insert_identity(principal.firebase_uid, self.ids(), principal.is_anonymous, now)
        user = (await repo.lock_identities([principal.firebase_uid]))[principal.firebase_uid]
        self._active(user)
        if principal.is_anonymous and not user["is_anonymous"]:
            raise ApiError("invalid_token", 401)
        if user["is_anonymous"]:
            await repo.ensure_guest_usage(user["id"], now, first_seen_ip_hash)
        return await repo.touch_identity(user, principal.is_anonymous, now)

    async def resolve_in_transaction(
        self,
        connection: AsyncConnection,
        principal: Principal,
        now: datetime,
        first_seen_ip_hash: bytes | None = None,
    ) -> RowMapping:
        """Resolve a principal on a caller-owned connection for atomic domain work."""
        return await self.resolve_identity(
            IdentityRepository(connection), principal, now, first_seen_ip_hash
        )

    async def me(
        self, principal: Principal, first_seen_ip_hash: bytes | None = None
    ) -> dict[str, Any]:
        now = self.clock()
        async with self.transaction() as connection:
            repo = IdentityRepository(connection)
            user = await self.resolve_identity(repo, principal, now, first_seen_ip_hash)
            used = await repo.creations(user["id"])
            limit = await repo.creation_limit(user["id"])
            return {
                "user_id": user["id"],
                "firebase_uid": user["firebase_uid"],
                "is_anonymous": user["is_anonymous"],
                "sign_in_provider": principal.sign_in_provider,
                "email_verified": principal.email_verified,
                "tier": user["tier"],
                "created_at": user["created_at"],
                "guest_creations": {
                    "limit": limit,
                    "used": used,
                    "remaining": max(0, limit - used),
                    "applicable": user["is_anonymous"],
                },
                "quotas": await repo.quota_summary(user["id"], now),
            }

    async def project_for_owner(self, principal: Principal, project_id: UUID) -> RowMapping:
        """Internal service seam; no Phase 02 resource endpoint is exposed."""
        async with self.transaction() as connection:
            repo = IdentityRepository(connection)
            user = await self.resolve_identity(repo, principal, self.clock())
            project = await repo.project(user["id"], project_id)
            if project is None:
                raise ApiError("not_found", 404)
            return project

    async def asset_for_owner(self, principal: Principal, asset_id: UUID) -> RowMapping:
        """Internal metadata only; storage keys must never become an HTTP response."""
        async with self.transaction() as connection:
            repo = IdentityRepository(connection)
            user = await self.resolve_identity(repo, principal, self.clock())
            asset = await repo.asset(user["id"], asset_id)
            if asset is None:
                raise ApiError("not_found", 404)
            return asset

    async def claim(
        self, destination: Principal, guest: Principal, request_id: str | None = None
    ) -> dict[str, Any]:
        now = self.clock()
        if destination.is_anonymous or not guest.is_anonymous:
            raise ApiError("invalid_claim", 400)
        if (
            guest.auth_time is None
            or not 0 <= now.timestamp() - guest.auth_time <= self.claim_max_age_seconds
        ):
            raise ApiError("stale_guest_token", 401)
        async with self.transaction() as connection:
            repo = IdentityRepository(connection)
            import sqlalchemy as sa

            await connection.execute(sa.select(sa.func.pg_advisory_xact_lock(70707001)))
            if self.erasure_check and (
                await self.erasure_check(destination.firebase_uid)
                or await self.erasure_check(guest.firebase_uid)
            ):
                raise ApiError("account_deleting", 403)
            principals = {guest.firebase_uid: guest, destination.firebase_uid: destination}
            for uid, principal in sorted(principals.items()):
                await repo.insert_identity(uid, self.ids(), principal.is_anonymous, now)
            users = await repo.lock_identities(list(principals))
            target = users[destination.firebase_uid]
            source = users[guest.firebase_uid]
            self._active(target)
            self._active(source, allow_merged=True)
            receipt = await repo.receipt(target["id"], source["id"])
            if receipt is not None:
                return receipt
            if source["merged_into_user_id"] is not None:
                raise ApiError("already_claimed", 409)
            if target["is_anonymous"]:
                await repo.ensure_guest_usage(target["id"], now)
            target = await repo.touch_identity(target, False, now)
            same_uid = target["id"] == source["id"]
            counts = {"projects": 0, "assets": 0, "jobs": 0, "reservations": 0}
            if not same_uid:
                if not source["is_anonymous"]:
                    raise ApiError("invalid_claim", 400)
                await repo.ensure_guest_usage(source["id"], now)
                reservations = await repo.reservation_count(source["id"])
                await repo.withdraw_guest_contributions(source["id"], now)
                counts = await repo.transfer(source["id"], target["id"], now)
                counts["reservations"] = reservations
                await repo.mark_merged(source["id"], target["id"], now)
            quota = await repo.quota_summary(target["id"], now)
            response = {
                "mode": "linked" if same_uid else "transferred",
                "source_user_id": str(source["id"]),
                "destination_user_id": str(target["id"]),
                "transferred": counts,
                "guest_creations_preserved": await repo.creations(source["id"]),
                "over_limit": quota["over_limit"],
            }
            await repo.save_receipt(target["id"], source["id"], response, now)
            await repo.audit_claim(target["id"], source["id"], counts, request_id, now)
            return response
