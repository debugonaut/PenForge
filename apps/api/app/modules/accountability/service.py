"""Private receipts and minimal admission records; no capture or document bodies."""

from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import Depends, Request
from sqlalchemy.dialects.postgresql import insert

from app.adapters.auth.base import Principal
from app.adapters.auth.dependencies import identity
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.privacy_reference import privacy_reference
from app.db.models import abuse_reports, audit_events, service_agreements
from app.modules.accountability.policy import current_policy
from app.modules.accountability.schemas import AbuseReportCreate, AgreementCreate
from app.modules.identity.service import IdentityService


def verified_account(principal: Principal) -> None:
    if principal.is_anonymous:
        raise ApiError("account_required", 403)
    if not principal.email_verified:
        raise ApiError("email_verification_required", 403)


class AccountabilityService:
    def __init__(self, identity: IdentityService, settings: Settings):
        self.identity = identity
        self.settings = settings

    async def access(
        self,
        principal: Principal,
        acceptance: AgreementCreate | None = None,
        *,
        operation: str | None = None,
        resource_id: UUID | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        policy = current_policy(self.settings)
        if acceptance or operation:
            verified_account(principal)
            if not policy["ready"]:
                raise ApiError("service_not_ready", 503)
        if acceptance and acceptance.policy_sha256 != policy["sha256"]:
            raise ApiError("policy_changed", 409)
        now = self.identity.clock()
        async with self.identity.transaction() as connection:
            user = await self.identity.resolve_in_transaction(connection, principal, now)
            if acceptance:
                await connection.execute(
                    insert(service_agreements)
                    .values(
                        id=uuid4(),
                        user_id=user["id"],
                        policy_version=policy["version"],
                        policy_sha256=policy["sha256"],
                        policy_document=policy["document"],
                        terms_accepted=True,
                        handwriting_rights_confirmed=True,
                        accepted_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["user_id", "policy_sha256"])
                )
            accepted_at = (
                await connection.execute(
                    sa.select(service_agreements.c.accepted_at).where(
                        service_agreements.c.user_id == user["id"],
                        service_agreements.c.policy_sha256 == policy["sha256"],
                    )
                )
            ).scalar_one_or_none()
            if operation:
                key = self.settings.privacy_key
                if key is None:
                    raise ApiError("dependency_unavailable", 503)
                if accepted_at is None:
                    raise ApiError("terms_required", 403)
                await connection.execute(
                    audit_events.insert().values(
                        actor_ref=privacy_reference(key, "subject", principal.firebase_uid).hex(),
                        action="service.request_admitted",
                        resource_type=operation,
                        resource_id=resource_id,
                        outcome="success",
                        request_id=request_id,
                        metadata={"policy_sha256": policy["sha256"]},
                    )
                )
            return {
                "policy_sha256": policy["sha256"],
                "accepted": accepted_at is not None,
                "accepted_at": accepted_at,
                "eligible": bool(
                    accepted_at
                    and not principal.is_anonymous
                    and principal.email_verified
                    and policy["ready"]
                ),
            }

    async def prune_reports(self, *, apply: bool = False) -> int:
        """Operator maintenance; fixed 90-day draft policy, content-free counts only."""
        cutoff = self.identity.clock() - timedelta(days=90)
        async with self.identity.transaction() as connection:
            expired = abuse_reports.c.created_at < cutoff
            count = await connection.scalar(
                sa.select(sa.func.count()).select_from(abuse_reports).where(expired)
            )
            if apply:
                await connection.execute(abuse_reports.delete().where(expired))
            return int(count or 0)

    async def report(self, body: AbuseReportCreate) -> UUID:
        report_id = uuid4()
        async with self.identity.transaction() as connection:
            # Public reports deliberately have no account-link or automatic penalty.
            await connection.execute(
                abuse_reports.insert().values(
                    id=report_id,
                    category=body.category,
                    details=body.details,
                    contact=body.contact,
                    resource_id=body.resource_id,
                    created_at=self.identity.clock(),
                )
            )
        return report_id


def service(request: Request) -> AccountabilityService:
    result = request.app.state.accountability_service
    if result is None:
        raise ApiError("dependency_unavailable", 503)
    return result


async def font_identity(
    request: Request, principal: Annotated[Principal, Depends(identity)]
) -> Principal:
    verified_account(principal)
    resource_id = None
    for name, value in request.path_params.items():
        if name.endswith("_id"):
            try:
                resource_id = UUID(str(value))
                break
            except ValueError:
                pass
    # Route name only: capability tokens, request bodies and URLs never enter audit.
    await service(request).access(
        principal,
        operation=request.scope["route"].name,
        resource_id=resource_id,
        request_id=getattr(request.state, "request_id", None),
    )
    return principal
