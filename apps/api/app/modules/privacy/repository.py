"""Owner-locked consent, deletion scope and authoritative inventory SQL."""

from uuid import UUID, uuid4

import sqlalchemy as sa

from app.db import models as m
from app.modules.jobs.service import lock_balances
from app.modules.privacy.schemas import PrivacyPolicy


class PrivacyRepository:
    def __init__(self, con):
        self.con = con

    async def now(self):
        return await self.con.scalar(sa.select(sa.func.clock_timestamp()))

    async def policy(self):
        value = await self.con.scalar(
            sa.select(m.system_settings.c.value).where(
                m.system_settings.c.key == "privacy_policy.v1"
            )
        )
        return PrivacyPolicy.model_validate(value or {})

    async def user(self, uid):
        return (
            (
                await self.con.execute(
                    sa.select(m.users).where(m.users.c.firebase_uid == uid).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

    async def request(self, request_id):
        return (
            (
                await self.con.execute(
                    sa.select(m.deletion_requests)
                    .where(m.deletion_requests.c.id == request_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

    async def consent(self, owner, active=False):
        query = sa.select(m.training_consents).where(m.training_consents.c.user_id == owner)
        if active:
            query = query.where(m.training_consents.c.withdrawn_at.is_(None))
        return (
            (
                await self.con.execute(
                    query.order_by(m.training_consents.c.granted_at.desc()).limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )

    async def withdraw(self, owner, now, project=None, *, close_grant=True):
        consents = sa.select(m.training_consents.c.id).where(m.training_consents.c.user_id == owner)
        pred = [m.dataset_contributions.c.consent_id.in_(consents)]
        if project is not None:
            pred.append(
                m.dataset_contributions.c.sample_id.in_(
                    sa.select(m.samples.c.id).where(m.samples.c.project_id == project)
                )
            )
        await self.con.execute(
            m.dataset_contributions.update()
            .where(*pred, m.dataset_contributions.c.status == "active")
            .values(status="withdrawn", withdrawn_at=now)
        )
        if close_grant:
            await self.con.execute(
                m.training_consents.update()
                .where(
                    m.training_consents.c.user_id == owner,
                    m.training_consents.c.withdrawn_at.is_(None),
                )
                .values(withdrawn_at=now)
            )

    async def create_job(self, deletion_id, now):
        jid = uuid4()
        await self.con.execute(
            m.jobs.insert().values(
                id=jid,
                owner_user_id=None,
                project_id=None,
                job_type="privacy_maintenance",
                payload={"schema_version": 1, "deletion_request_id": str(deletion_id)},
                payload_schema_version=1,
                max_attempts=1000000,
                priority=2,
                run_after=now,
            )
        )
        return jid

    async def scopes(self, row):
        owners = [UUID(v) for v in row["target_users"]]
        if row["scope"] == "project":
            return owners, [row["project_id"]]
        projects = list(
            (
                await self.con.execute(
                    sa.select(m.projects.c.id).where(m.projects.c.owner_user_id.in_(owners))
                )
            ).scalars()
        )
        return owners, projects

    async def assets(self, row):
        if row["scope"] == "contributions_only":
            return []
        owners, projects = await self.scopes(row)
        pred = (
            m.assets.c.project_id.in_(projects)
            if row["scope"] == "project"
            else m.assets.c.owner_user_id.in_(owners)
        )
        return list(
            (
                await self.con.execute(sa.select(m.assets).where(pred).order_by(m.assets.c.id))
            ).mappings()
        )

    async def contributions(self, row):
        owners, projects = await self.scopes(row)
        pred = m.training_consents.c.user_id.in_(owners)
        query = sa.select(m.dataset_contributions).outerjoin(
            m.training_consents, m.training_consents.c.id == m.dataset_contributions.c.consent_id
        )
        # Historical samples still associate withdrawn/claimed guest contributions.
        if row["scope"] == "project":
            pred = m.dataset_contributions.c.sample_id.in_(
                sa.select(m.samples.c.id).where(m.samples.c.project_id.in_(projects))
            )
        if row["scope"] == "contributions_only":
            pred = m.dataset_contributions.c.consent_id.in_([UUID(v) for v in row["consent_ids"]])
        return list(
            (
                await self.con.execute(query.where(pred).order_by(m.dataset_contributions.c.id))
            ).mappings()
        )

    async def cancel_jobs(self, row, coordinator):
        if row["scope"] == "contributions_only":
            return True
        owners, projects = await self.scopes(row)
        pred = (
            m.jobs.c.project_id.in_(projects)
            if row["scope"] == "project"
            else m.jobs.c.owner_user_id.in_(owners)
        )
        jobs = list(
            (
                await self.con.execute(
                    sa.select(m.jobs)
                    .where(pred, m.jobs.c.state.in_(["queued", "running", "cancel_requested"]))
                    .order_by(m.jobs.c.id)
                    .with_for_update()
                )
            ).mappings()
        )
        now = await self.now()
        for job in jobs:
            if job["state"] == "queued" or (
                job["lease_expires_at"] and job["lease_expires_at"] <= now
            ):
                await coordinator.terminal(self.con, job, "canceled", "canceled")
            elif job["state"] == "running":
                await self.con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == job["id"])
                    .values(state="cancel_requested", cancel_requested=True)
                )
        return all(
            j["state"] == "queued" or j["lease_expires_at"] and j["lease_expires_at"] <= now
            for j in jobs
        )

    async def lock_targets(self, row):
        owners, projects = await self.scopes(row)
        await self.con.execute(
            sa.select(m.users)
            .where(m.users.c.id.in_(owners))
            .order_by(m.users.c.id)
            .with_for_update()
        )
        await self.con.execute(
            sa.select(m.projects)
            .where(m.projects.c.id.in_(projects))
            .order_by(m.projects.c.id)
            .with_for_update()
        )

    async def settle_removed(self, assets, row):
        owners, projects = await self.scopes(row)
        for owner in sorted(owners):
            await lock_balances(self.con, owner)
        asset_ids = [a["id"] for a in assets]
        jobs = sa.select(m.jobs.c.id).where(m.jobs.c.project_id.in_(projects))
        pred = sa.or_(
            m.quota_reservations.c.asset_id.in_(asset_ids), m.quota_reservations.c.job_id.in_(jobs)
        )
        if row["scope"] == "account":
            pred = sa.or_(pred, m.quota_reservations.c.user_id.in_(owners))
        reservations = list(
            (
                await self.con.execute(
                    sa.select(m.quota_reservations).where(pred).with_for_update()
                )
            ).mappings()
        )
        for r in reservations:
            reserved = r["amount"] if r["status"] == "reserved" else 0
            committed = (
                (r["committed_amount"] or 0)
                if r["status"] == "committed" and r["resource"] == "storage_bytes"
                else 0
            )
            await self.con.execute(
                m.quota_balances.update()
                .where(m.quota_balances.c.id == r["balance_id"])
                .values(
                    reserved=sa.func.greatest(0, m.quota_balances.c.reserved - reserved),
                    committed=sa.func.greatest(0, m.quota_balances.c.committed - committed),
                )
            )
        await self.con.execute(m.quota_reservations.delete().where(pred))

    async def purge(self, row):
        owners, projects = await self.scopes(row)
        assets = await self.assets(row)
        await self.settle_removed(assets, row)
        # Remove non-cascading references in the same transaction as parent cascades.
        await self.con.execute(
            m.job_checkpoints.delete().where(m.job_checkpoints.c.project_id.in_(projects))
        )
        await self.con.execute(
            m.idempotency_keys.delete().where(m.idempotency_keys.c.user_id.in_(owners))
        )
        if row["scope"] == "account":
            # Mutable account drafts/documents hold no immutable lineage. They are
            # erased with the account before identity removal, alongside captures.
            await self.con.execute(
                m.writing_documents.delete().where(m.writing_documents.c.owner_user_id.in_(owners))
            )
        count = (
            await self.con.execute(m.projects.delete().where(m.projects.c.id.in_(projects)))
        ).rowcount
        if row["scope"] == "account":
            standalone = sa.and_(
                m.assets.c.owner_user_id.in_(owners), m.assets.c.project_id.is_(None)
            )
            await self.con.execute(
                m.assets.update()
                .where(standalone, m.assets.c.state.not_in(["deleted", "deleting"]))
                .values(state="deleting")
            )
            await self.con.execute(
                m.assets.update()
                .where(standalone, m.assets.c.state == "deleting")
                .values(state="deleted")
            )
            await self.con.execute(m.assets.delete().where(standalone))
            await self.con.execute(m.jobs.delete().where(m.jobs.c.owner_user_id.in_(owners)))
            count += await self.con.scalar(sa.select(sa.func.penforge_purge_account(row["id"])))
        return count
