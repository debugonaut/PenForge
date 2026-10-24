"""Public queue coordination seam shared by all three existing handlers."""

import asyncio
import json
import math
import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from penforge_contracts import content_hash

from app.core.errors import ApiError
from app.db import models as m
from app.modules.jobs import repository


async def queue_candidates(connection, types, versions, memory, job_id=None):
    return await repository.candidates(connection, types, versions, memory, job_id)


async def admit(connection, job_type):
    return await repository.admission(connection, job_type)


TERMINAL = {"succeeded", "failed", "canceled"}


def backoff(attempt, base=30, cap=600, random_source=random.random):
    return min(cap, base * 2 ** min(20, max(0, attempt - 1)) * (0.8 + 0.4 * random_source()))


def failure_class(code):
    if code in {"dependency_unavailable", "worker_interrupted", "lease_expired"}:
        return "retryable_infra"
    if code in {"resource_unavailable", "capacity_exceeded", "decode_resource_limit"}:
        return "resource_unavailable"
    if code in {"unsupported_payload", "unsupported_schema_version"}:
        return "unsupported_schema"
    if code in {"validation_failed", "quality_gate_failed"}:
        return "validation_failure"
    if code == "needs_better_input":
        return "needs_better_input"
    if code == "canceled":
        return "canceled"
    if code == "poison_suspected":
        return "poison_job"
    return "invalid_input"


async def set_worker_fence(connection, job, holder):
    await connection.execute(
        sa.select(
            sa.func.set_config(
                "penforge.worker_fence",
                json.dumps(
                    {
                        "job_id": str(job["id"]),
                        "attempt": job["attempts"],
                        "holder": holder,
                        "expires_at": job["lease_expires_at"].isoformat(),
                    }
                ),
                True,
            )
        )
    )


async def live_job(connection, job, holder, *, allow_cancel=False):
    """All worker operations use holder AND monotonic attempt AND live scope/lease."""
    owner = (
        (
            await connection.execute(
                sa.select(m.users).where(m.users.c.id == job["owner_user_id"]).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    project = (
        (
            await connection.execute(
                sa.select(m.projects)
                .where(
                    m.projects.c.id == job["project_id"],
                    m.projects.c.owner_user_id == job["owner_user_id"],
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    current = (
        (
            await connection.execute(
                sa.select(m.jobs)
                .where(m.jobs.c.id == job["id"], m.jobs.c.owner_user_id == job["owner_user_id"])
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    now = await connection.scalar(sa.select(sa.func.clock_timestamp()))
    if (
        not owner
        or owner["account_state"] != "active"
        or owner["merged_into_user_id"]
        or not project
        or project["status"] == "deleting"
        or not current
        or current["state"]
        not in ({"running", "cancel_requested"} if allow_cancel else {"running"})
        or (current["cancel_requested"] and not allow_cancel)
        or current["lease_holder"] != holder
        or current["attempts"] != job["attempts"]
        or not current["lease_expires_at"]
        or current["lease_expires_at"] <= now
    ):
        return None
    await set_worker_fence(connection, current, holder)
    return current


async def settle(connection, job, actual=None, *, include_assets=False):
    """Idempotent measured settlement. An over-reservation usage fails before writes."""
    actual = actual or {}
    await lock_balances(connection, job["owner_user_id"])
    predicate = [
        m.quota_reservations.c.job_id == job["id"],
        m.quota_reservations.c.user_id == job["owner_user_id"],
        m.quota_reservations.c.status == "reserved",
    ]
    if not include_assets:
        predicate.append(m.quota_reservations.c.asset_id.is_(None))
    refs = sa.select(m.quota_reservations.c.balance_id).where(*predicate)
    await connection.execute(
        sa.select(m.quota_balances)
        .where(m.quota_balances.c.id.in_(refs))
        .order_by(m.quota_balances.c.id)
        .with_for_update()
    )
    rows = list(
        (
            await connection.execute(
                sa.select(m.quota_reservations)
                .where(*predicate)
                .order_by(m.quota_reservations.c.id)
                .with_for_update()
            )
        ).mappings()
    )
    for row in rows:
        used = actual.get(row["resource"], 0) if row["asset_id"] is None else 0
        if not isinstance(used, int) or not 0 <= used <= row["amount"]:
            raise ApiError("quota_exceeded", 429)
    for row in rows:
        used = actual.get(row["resource"], 0) if row["asset_id"] is None else 0
        await connection.execute(
            m.quota_balances.update()
            .where(m.quota_balances.c.id == row["balance_id"])
            .values(
                reserved=m.quota_balances.c.reserved - row["amount"],
                committed=m.quota_balances.c.committed + used,
            )
        )
        await connection.execute(
            m.quota_reservations.update()
            .where(
                m.quota_reservations.c.id == row["id"], m.quota_reservations.c.status == "reserved"
            )
            .values(
                status="committed" if used else "released",
                committed_amount=used,
                settled_at=sa.func.clock_timestamp(),
            )
        )


class JobsService:
    def __init__(self, uploads, *, random_source=random.random, clock=None):
        self.uploads, self.engine, self.identity = uploads, uploads.engine, uploads.identity_service
        self.random, self.clock = random_source, clock

    async def now(self, con):
        return (
            self.clock() if self.clock else await con.scalar(sa.select(sa.func.clock_timestamp()))
        )

    async def owned(self, con, principal, job_id):
        owner = await self.identity.resolve_in_transaction(con, principal, datetime.now(UTC))
        job = (
            (
                await con.execute(
                    sa.select(m.jobs).where(
                        m.jobs.c.id == job_id, m.jobs.c.owner_user_id == owner["id"]
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if job is None:
            raise ApiError("not_found", 404)
        project = (
            (
                await con.execute(
                    sa.select(m.projects).where(
                        m.projects.c.id == job["project_id"],
                        m.projects.c.owner_user_id == owner["id"],
                        m.projects.c.status != "deleting",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if project is None:
            raise ApiError("not_found", 404)
        return job

    async def get(self, principal, job_id):
        async with self.engine.begin() as con:
            job = await self.owned(con, principal, job_id)
            snapshot = await con.scalar(
                sa.select(m.job_events.c.state_snapshot)
                .where(
                    m.job_events.c.job_id == job["id"], m.job_events.c.state_snapshot.is_not(None)
                )
                .order_by(m.job_events.c.id.desc())
                .limit(1)
            )
            return snapshot or job_view(job)

    async def events(self, principal, job_id, after=0, *, validate_cursor=False):
        async with self.engine.begin() as con:
            await self.owned(con, principal, job_id)
            if (
                validate_cursor
                and after
                and not await con.scalar(
                    sa.select(
                        sa.exists().where(
                            m.job_events.c.job_id == job_id, m.job_events.c.id == after
                        )
                    )
                )
            ):
                raise ApiError("validation_error", 400)
            return list(
                (
                    await con.execute(
                        sa.select(m.job_events.c.id, m.job_events.c.state_snapshot)
                        .where(
                            m.job_events.c.job_id == job_id,
                            m.job_events.c.id > after,
                            m.job_events.c.state_snapshot.is_not(None),
                        )
                        .order_by(m.job_events.c.id)
                        .limit(100)
                    )
                ).mappings()
            )

    async def open_stream(self, principal, job_id, after=0):
        from uuid import uuid4

        async with self.engine.begin() as con:
            await con.execute(sa.select(sa.func.pg_advisory_xact_lock(70606002)))
            job = await self.owned(con, principal, job_id)
            if after and not await con.scalar(
                sa.select(
                    sa.exists().where(m.job_events.c.id == after, m.job_events.c.job_id == job_id)
                )
            ):
                raise ApiError("validation_error", 400)
            await con.execute(
                m.job_streams.delete().where(
                    m.job_streams.c.expires_at <= sa.func.clock_timestamp()
                )
            )
            config = await repository.policy(con)
            global_count = await con.scalar(sa.select(sa.func.count()).select_from(m.job_streams))
            owner_count = await con.scalar(
                sa.select(sa.func.count())
                .select_from(m.job_streams)
                .where(m.job_streams.c.owner_user_id == job["owner_user_id"])
            )
            if (
                global_count >= config.stream_global_limit
                or owner_count >= config.stream_owner_limit
            ):
                raise ApiError(
                    "capacity_exceeded", 503, headers={"Retry-After": str(config.retry_after)}
                )
            stream_id = uuid4()
            await con.execute(
                m.job_streams.insert().values(
                    id=stream_id,
                    job_id=job_id,
                    owner_user_id=job["owner_user_id"],
                    expires_at=await self.now(con)
                    + timedelta(seconds=config.stream_lifetime_seconds + 5),
                )
            )
            return stream_id, config.stream_lifetime_seconds

    async def close_stream(self, stream_id):
        async with self.engine.begin() as con:
            await con.execute(m.job_streams.delete().where(m.job_streams.c.id == stream_id))

    async def event(self, con, job, old, state, *, code=None, kind="state"):
        await con.execute(
            m.job_events.insert().values(
                job_id=job["id"],
                kind=kind,
                from_state=old,
                to_state=state,
                stage=job["stage"],
                metadata={
                    "attempt": job["attempts"],
                    **({"error_code": code, "failure_class": failure_class(code)} if code else {}),
                },
            )
        )

    async def terminal(self, con, job, state, code):
        """Settle and transition under the same locks/transaction as cancel or reap."""
        if job["state"] in TERMINAL:
            return
        now = await self.now(con)
        await lock_balances(con, job["owner_user_id"])
        if state == "canceled" and job["state"] == "running":
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == job["id"])
                .values(state="cancel_requested", cancel_requested=True)
            )
        if job["job_type"] == "compile_export":
            await con.execute(
                m.font_exports.update()
                .where(m.font_exports.c.job_id == job["id"], m.font_exports.c.status == "pending")
                .values(
                    status="failed",
                    validation_status="failed",
                    validation_report={"status": "failed", "error_code": code},
                    finalized_at=now,
                    finalized_attempt=job["attempts"],
                )
            )
        # Only unaccepted staged assets are eligible; accepted historical assets never enter here.
        ids = list(job["checkpoint"].get("staged_asset_ids", [])) + list(
            job["checkpoint"].get("checkpoint_staged_asset_ids", [])
        )
        for outputs in job["checkpoint"].get("outputs", {}).values():
            ids.extend(outputs.values())
        if job["job_type"] == "asset_verify":
            ids.append(job["payload"]["asset_id"])
        for aid in sorted(set(ids)):
            asset = (
                (
                    await con.execute(
                        sa.select(m.assets)
                        .where(
                            m.assets.c.id == UUID(aid),
                            m.assets.c.owner_user_id == job["owner_user_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if asset and asset["state"] not in {"ready", "deleted"}:
                await asyncio.to_thread(self.uploads.storage.delete_object, asset["storage_key"])
                if asset["state"] == "uploaded":
                    await con.execute(
                        m.assets.update()
                        .where(m.assets.c.id == asset["id"])
                        .values(state="verifying")
                    )
                await con.execute(
                    m.assets.update()
                    .where(m.assets.c.id == asset["id"])
                    .values(
                        state="rejected" if asset["kind"] == "original_upload" else "expired",
                        reject_reason=code,
                        cleanup_completed_at=now,
                    )
                )
                # Reservations for export ledgers have no job link in older releases.
                await self.uploads.settle_artifact_in_transaction(
                    con, job["owner_user_id"], asset["id"], None
                )
        if job["job_type"] == "asset_verify":
            # Derivative keys are deterministic but not yet accepted asset rows.
            source = (
                (
                    await con.execute(
                        sa.select(m.assets).where(m.assets.c.id == UUID(job["payload"]["asset_id"]))
                    )
                )
                .mappings()
                .one_or_none()
            )
            if source:
                prefix = source["storage_key"].rsplit("/orig/", 1)[0]
                for attempt in range(1, job["attempts"] + 1):
                    await asyncio.to_thread(
                        self.uploads.storage.delete_object,
                        f"{prefix}/thumb/{source['id']}/a{attempt}.png",
                    )
        measured = math.ceil(job["usage"].get("wall_seconds", 0))
        reserved_cap = await con.scalar(
            sa.select(sa.func.min(m.quota_reservations.c.amount)).where(
                m.quota_reservations.c.job_id == job["id"],
                m.quota_reservations.c.asset_id.is_(None),
                m.quota_reservations.c.resource == "compute_seconds",
                m.quota_reservations.c.status == "reserved",
            )
        )
        usage = dict(job["usage"])
        if reserved_cap is not None and measured > reserved_cap:
            usage["reservation_exceeded"] = True
            usage["uncommitted_compute_seconds"] = measured - reserved_cap
            code = "quota_exceeded"
        await settle(
            con,
            job,
            {
                "compute_seconds": min(measured, reserved_cap)
                if reserved_cap is not None
                else measured
            },
        )
        await con.execute(
            m.jobs.update()
            .where(m.jobs.c.id == job["id"])
            .values(
                state=state,
                error_code=code,
                usage=usage,
                lease_holder=None,
                lease_expires_at=None,
                finished_at=now,
            )
        )
        await self.event(con, job, job["state"], state, code=code)

    async def cancel(self, principal, job_id):
        async with self.engine.begin() as con:
            job = await self.owned(con, principal, job_id)
            job = (
                (
                    await con.execute(
                        sa.select(m.jobs).where(m.jobs.c.id == job_id).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if job["state"] == "queued":
                await self.terminal(con, job, "canceled", "canceled")
            elif job["state"] == "running":
                await con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == job_id)
                    .values(state="cancel_requested", cancel_requested=True)
                )
                await self.event(con, job, "running", "cancel_requested")
        return await self.get(principal, job_id)

    async def boundary(self, job, holder, *, shutdown=False):
        async with self.engine.begin() as con:
            current = await live_job(con, job, holder, allow_cancel=True)
            if current is None:
                return False
            if current["cancel_requested"]:
                await self.terminal(con, current, "canceled", "canceled")
                return True
            if shutdown:
                # The fencing counter NEVER decreases; grant one replacement attempt.
                await con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == current["id"])
                    .values(
                        state="queued",
                        max_attempts=m.jobs.c.max_attempts + 1,
                        lease_holder=None,
                        lease_expires_at=None,
                        run_after=await self.now(con),
                    )
                )
                await self.event(con, current, "running", "queued", code="worker_interrupted")
                return True
        return False

    async def reconcile_reservations(self):
        released = 0
        async with self.engine.begin() as con:
            orphan = (
                sa.select(m.quota_reservations.c.balance_id)
                .outerjoin(m.jobs, m.jobs.c.id == m.quota_reservations.c.job_id)
                .where(
                    m.quota_reservations.c.status == "reserved",
                    m.quota_reservations.c.asset_id.is_(None),
                    sa.or_(
                        m.jobs.c.state.in_(TERMINAL),
                        sa.and_(
                            m.quota_reservations.c.job_id.is_(None),
                            m.quota_reservations.c.expires_at <= sa.func.clock_timestamp(),
                        ),
                    ),
                )
            )
            await con.execute(
                sa.select(m.quota_balances)
                .where(m.quota_balances.c.id.in_(orphan))
                .order_by(m.quota_balances.c.id)
                .with_for_update()
            )
            rows = list(
                (
                    await con.execute(
                        sa.select(m.quota_reservations)
                        .where(
                            m.quota_reservations.c.balance_id.in_(orphan),
                            m.quota_reservations.c.status == "reserved",
                            m.quota_reservations.c.asset_id.is_(None),
                        )
                        .order_by(m.quota_reservations.c.id)
                        .with_for_update()
                    )
                ).mappings()
            )
            for row in rows:
                state = (
                    await con.scalar(sa.select(m.jobs.c.state).where(m.jobs.c.id == row["job_id"]))
                    if row["job_id"]
                    else None
                )
                if not row["job_id"] and row["expires_at"] > await self.now(con):
                    continue
                if row["job_id"] and state not in TERMINAL:
                    continue
                await con.execute(
                    m.quota_balances.update()
                    .where(m.quota_balances.c.id == row["balance_id"])
                    .values(reserved=m.quota_balances.c.reserved - row["amount"])
                )
                await con.execute(
                    m.quota_reservations.update()
                    .where(m.quota_reservations.c.id == row["id"])
                    .values(
                        status="expired", committed_amount=0, settled_at=sa.func.clock_timestamp()
                    )
                )
                released += 1
        return {"released_orphan_reservations": released}

    async def clear_analysis_staging(self, con, job):
        ids = list(job["checkpoint"].get("checkpoint_staged_asset_ids", []))
        if job["job_type"] == "analyze_samples":
            ids.extend(job["checkpoint"].get("staged_asset_ids", []))
        for aid in ids:
            asset = (
                (
                    await con.execute(
                        sa.select(m.assets)
                        .where(
                            m.assets.c.id == UUID(aid),
                            m.assets.c.owner_user_id == job["owner_user_id"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if asset and asset["state"] == "pending_upload":
                await asyncio.to_thread(self.uploads.storage.delete_object, asset["storage_key"])
                await con.execute(
                    m.assets.update()
                    .where(m.assets.c.id == asset["id"])
                    .values(state="expired", cleanup_completed_at=await self.now(con))
                )
                await self.uploads.settle_artifact_in_transaction(
                    con, job["owner_user_id"], asset["id"], None
                )

    async def reap_queued(self, types=None):
        async with self.engine.begin() as con:
            query = (
                sa.select(m.jobs)
                .where(m.jobs.c.state == "queued", m.jobs.c.job_type != "privacy_maintenance")
                .order_by(m.jobs.c.created_at)
                .limit(100)
            )
            if types:
                query = query.where(m.jobs.c.job_type.in_(types))
            rows = list((await con.execute(query)).mappings())
        for row in rows:
            async with self.engine.begin() as con:
                owner = (
                    (
                        await con.execute(
                            sa.select(m.users)
                            .where(m.users.c.id == row["owner_user_id"])
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                project = (
                    (
                        await con.execute(
                            sa.select(m.projects)
                            .where(m.projects.c.id == row["project_id"])
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                job = (
                    (
                        await con.execute(
                            sa.select(m.jobs).where(m.jobs.c.id == row["id"]).with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if not job or job["state"] != "queued":
                    continue
                invalid = (
                    not owner
                    or owner["account_state"] != "active"
                    or owner["merged_into_user_id"]
                    or not project
                    or project["status"] == "deleting"
                    or project["owner_user_id"] != job["owner_user_id"]
                )
                planned = [
                    UUID(aid)
                    for output in job["checkpoint"].get("outputs", {}).values()
                    for aid in output.values()
                ]
                if planned:
                    invalid = invalid or bool(
                        await con.scalar(
                            sa.select(
                                sa.exists().where(
                                    m.assets.c.id.in_(planned),
                                    m.assets.c.state == "pending_upload",
                                    m.assets.c.expires_at <= sa.func.clock_timestamp(),
                                )
                            )
                        )
                    )
                if invalid:
                    await self.terminal(con, job, "canceled", "canceled")

    async def retry_or_fail(
        self, job, holder, code, *, retryable=False, usage_seconds=0, base=None
    ):
        async with self.engine.begin() as con:
            current = await live_job(con, job, holder)
            if current is None:
                return False
            config = await repository.policy(con)
            checkpoint = dict(current["checkpoint"])
            fingerprint = content_hash(
                {
                    "stage": current["stage"],
                    "input": job.get("_failure_input_hash", current["payload"]),
                    "code": code,
                }
            )
            failures = list(checkpoint.get("failures", []))
            failures.append(fingerprint)
            poison = retryable and failures.count(fingerprint) >= 2
            checkpoint["failures"] = failures[-10:]
            usage = dict(current["usage"])
            attempt_usage = dict(usage.get("attempt_seconds", {}))
            attempt_usage[str(job["attempts"])] = max(
                attempt_usage.get(str(job["attempts"]), 0), usage_seconds
            )
            usage.update(attempt_seconds=attempt_usage, wall_seconds=sum(attempt_usage.values()))
            await con.execute(
                m.jobs.update()
                .where(m.jobs.c.id == current["id"])
                .values(checkpoint=checkpoint, usage=usage)
            )
            current = dict(current) | {"checkpoint": checkpoint, "usage": usage}
            if retryable and not poison and current["attempts"] < current["max_attempts"]:
                await self.clear_analysis_staging(con, current)
                checkpoint.pop("staged_asset_ids", None)
                checkpoint.pop("checkpoint_staged_asset_ids", None)
                delay = backoff(
                    current["attempts"],
                    config.retry_base if base is None else base,
                    config.retry_cap,
                    self.random,
                )
                await con.execute(
                    m.jobs.update()
                    .where(m.jobs.c.id == current["id"])
                    .values(
                        state="queued",
                        checkpoint=checkpoint,
                        run_after=await self.now(con) + timedelta(seconds=delay),
                        lease_holder=None,
                        lease_expires_at=None,
                        error_code=code,
                    )
                )
                await self.event(con, current, "running", "queued", code=code)
            else:
                await self.terminal(con, current, "failed", "poison_suspected" if poison else code)
        return True

    async def reap(self, *, types=None, base=None):
        await self.reap_queued(types)
        async with self.engine.begin() as con:
            query = (
                sa.select(m.jobs)
                .where(
                    m.jobs.c.state.in_(["running", "cancel_requested"]),
                    m.jobs.c.job_type != "privacy_maintenance",
                    m.jobs.c.lease_expires_at <= sa.func.clock_timestamp(),
                )
                .order_by(m.jobs.c.lease_expires_at)
                .limit(100)
            )
            if types:
                query = query.where(m.jobs.c.job_type.in_(types))
            rows = list((await con.execute(query)).mappings())
        count = 0
        for row in rows:
            async with self.engine.begin() as con:
                # Same lock order as live_job; expired lease is deliberately not renewable.
                owner = (
                    (
                        await con.execute(
                            sa.select(m.users)
                            .where(m.users.c.id == row["owner_user_id"])
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                project = (
                    (
                        await con.execute(
                            sa.select(m.projects)
                            .where(m.projects.c.id == row["project_id"])
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                job = (
                    (
                        await con.execute(
                            sa.select(m.jobs).where(m.jobs.c.id == row["id"]).with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                now = await self.now(con)
                if (
                    not job
                    or job["state"] not in {"running", "cancel_requested"}
                    or job["lease_expires_at"] > now
                ):
                    continue
                usage = dict(job["usage"])
                unknown = set(usage.get("unmeasured_attempts", []))
                unknown.add(job["attempts"])
                usage["unmeasured_attempts"] = sorted(unknown)
                await con.execute(
                    m.jobs.update().where(m.jobs.c.id == job["id"]).values(usage=usage)
                )
                job = dict(job) | {"usage": usage}
                canceled = (
                    job["cancel_requested"]
                    or not owner
                    or owner["account_state"] != "active"
                    or owner["merged_into_user_id"]
                    or not project
                    or project["status"] == "deleting"
                    or project["owner_user_id"] != job["owner_user_id"]
                )
                if canceled or job["attempts"] >= job["max_attempts"]:
                    await self.terminal(
                        con,
                        job,
                        "canceled" if canceled else "failed",
                        "canceled" if canceled else "lease_expired_max_attempts",
                    )
                else:
                    await self.clear_analysis_staging(con, job)
                    checkpoint = dict(job["checkpoint"])
                    checkpoint.pop("staged_asset_ids", None)
                    checkpoint.pop("checkpoint_staged_asset_ids", None)
                    config = await repository.policy(con)
                    delay = backoff(
                        job["attempts"],
                        config.retry_base if base is None else base,
                        config.retry_cap,
                        self.random,
                    )
                    await con.execute(
                        m.jobs.update()
                        .where(m.jobs.c.id == job["id"])
                        .values(
                            state="queued",
                            lease_holder=None,
                            lease_expires_at=None,
                            run_after=now + timedelta(seconds=delay),
                            error_code="lease_expired",
                            checkpoint=checkpoint,
                        )
                    )
                    await self.event(con, job, job["state"], "queued", code="lease_expired")
                count += 1
        return count


def checkpoint_store(uploads):
    from app.modules.jobs.checkpoints import Checkpoints

    return Checkpoints(JobsService(uploads))


def job_view(job):
    checkpoint = job["checkpoint"] or {}
    kind = job["job_type"]
    basis = {
        "asset_verify": "verified_checks",
        "analyze_samples": "assessed_samples",
        "compile_export": "validation_layers"
        if job["stage"] == "validating"
        else "compiled_formats",
    }.get(kind, "validated_units")
    total = checkpoint.get(
        "total_units",
        4 if kind == "asset_verify" else len(job["payload"].get("sample_ids", [])) or None,
    )
    completed = checkpoint.get("completed_units", 0)
    if total is not None and completed > total:
        total = None
    state = job["state"]
    return dict(
        job_id=str(job["id"]),
        job_type=kind,
        state=state,
        stage=job["stage"],
        attempts=job["attempts"],
        progress=dict(
            completed_units=completed,
            total_units=total,
            basis=basis,
            indeterminate=total is None,
            stage_status="finished"
            if state in TERMINAL
            else "waiting"
            if state == "queued"
            else "active",
            denominator_change_reason=checkpoint.get("denominator_change_reason"),
            plan_revision=checkpoint.get("plan_revision", 1),
        ),
        eta_seconds=None,
        eta_reason="insufficient_data",
        quality_policy="accuracy_first",
        queue_wait=dict(
            entered_at=job["created_at"].isoformat(),
            eligible_at=job["run_after"].isoformat(),
            started_at=job["started_at"].isoformat() if job["started_at"] else None,
        ),
        active_processing=dict(measured_seconds=job["usage"].get("wall_seconds")),
        user_review_wait=state == "succeeded" and kind == "analyze_samples",
        requires_user_action=state == "succeeded" and kind == "analyze_samples",
        error_code=job["error_code"],
        retrying=state == "queued" and job["attempts"] > 0,
        asset_id=job["payload"].get("asset_id"),
        result_asset_id=job["payload"].get("asset_id") if state == "succeeded" else None,
    )


async def reserve_resources(connection, owner, job, amounts, now):
    return await repository.reserve(connection, owner, job, amounts, now)


async def measured_usage(uploads, job, holder, seconds):
    async with uploads.engine.begin() as con:
        current = await live_job(con, job, holder, allow_cancel=True)
        if not current:
            return False
        usage = dict(current["usage"])
        attempts = dict(usage.get("attempt_seconds", {}))
        attempts[str(job["attempts"])] = max(attempts.get(str(job["attempts"]), 0), seconds)
        usage.update(attempt_seconds=attempts, wall_seconds=sum(attempts.values()))
        await con.execute(m.jobs.update().where(m.jobs.c.id == job["id"]).values(usage=usage))
        return True


async def lock_balances(connection, owner):
    await connection.execute(
        sa.select(m.quota_balances.c.id)
        .where(sa.or_(m.quota_balances.c.user_id == owner, m.quota_balances.c.scope == "global"))
        .order_by(m.quota_balances.c.id)
        .with_for_update()
    )


async def configure_balances(connection, owner, resources, now):
    await repository.configured_balances(connection, owner, resources, now)
    await lock_balances(connection, owner)
