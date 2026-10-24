"""Shared scheduling SQL; never a second queue. Callers own transactions."""

import sqlalchemy as sa
from penforge_contracts import (
    AnalyzeSamplesJob,
    AssetVerifyJob,
    CompileExportJob,
    PrivacyMaintenanceJob,
    parse_analysis_payload,
)
from pydantic import BaseModel, ValidationError

from app.db import models as m
from app.modules.jobs.policy import QueuePolicy

# Every claim acquires this before user/project/job row locks. Other operations never
# acquire it while holding those rows. It serializes caps even across job types/processes.
CLAIM_LOCK = 70606001


async def policy(connection):
    value = await connection.scalar(
        sa.select(m.system_settings.c.value).where(m.system_settings.c.key == "queue_policy.v1")
    )
    return QueuePolicy.model_validate(value or {})


async def admission(connection, job_type):
    from app.core.errors import ApiError

    config = await policy(connection)
    if config.degradation_level == 4:
        raise ApiError("capacity_exceeded", 503, headers={"Retry-After": str(config.retry_after)})
    return config.admission_resources.get(job_type, {})


def claim_query(types, versions, memory, config, job_id=None):
    j = m.jobs
    r = j.alias("running")
    running = (
        sa.select(sa.func.count())
        .select_from(r)
        .where(
            r.c.owner_user_id == j.c.owner_user_id, r.c.state.in_(["running", "cancel_requested"])
        )
        .correlate(j)
        .scalar_subquery()
    )
    history = j.alias("dispatch_history")
    last_served = sa.func.coalesce(
        sa.select(sa.func.max(history.c.last_heartbeat_at))
        .where(history.c.owner_user_id == j.c.owner_user_id)
        .correlate(j)
        .scalar_subquery(),
        sa.literal_column("'1970-01-01'::timestamptz"),
    )
    # A bounded aging boost lets old lower classes reach the highest class. Callers
    # cannot supply priority. SQL avoids casts of arbitrary JSON resource profiles.
    age = sa.func.floor(
        sa.extract("epoch", sa.func.clock_timestamp() - j.c.created_at) / config.aging_seconds
    )
    effective = sa.func.least(2, sa.func.greatest(-2, j.c.priority) + age)
    memory_text = j.c.resource_profile["mem_mb"].astext
    memory_value = sa.case(
        (memory_text.op("~")(r"^[0-9]{1,6}$"), sa.cast(memory_text, sa.Integer)), else_=0
    )
    owner_cap = 1 if config.degradation_level >= 3 else config.owner_running
    query = (
        sa.select(j)
        .outerjoin(m.projects, m.projects.c.id == j.c.project_id)
        .outerjoin(m.users, m.users.c.id == j.c.owner_user_id)
        .where(
            j.c.state == "queued",
            j.c.run_after <= sa.func.clock_timestamp(),
            j.c.job_type.in_(types),
            j.c.payload_schema_version.in_(versions),
            sa.or_(
                sa.and_(
                    j.c.job_type == "privacy_maintenance",
                    j.c.owner_user_id.is_(None),
                    j.c.project_id.is_(None),
                ),
                sa.and_(
                    m.projects.c.script_code.in_(["Latn", "Deva"]),
                    m.projects.c.owner_user_id == j.c.owner_user_id,
                    m.projects.c.status != "deleting",
                    m.users.c.account_state == "active",
                    m.users.c.merged_into_user_id.is_(None),
                ),
            ),
            memory_value <= memory,
            sa.or_(memory_text.is_(None), memory_text.op("~")(r"^[0-9]{1,6}$")),
            sa.or_(
                j.c.resource_profile["quality_policy"].astext.is_(None),
                j.c.resource_profile["quality_policy"].astext == "accuracy_first",
            ),
            sa.or_(
                j.c.resource_profile["profile"].astext.is_(None),
                j.c.resource_profile["profile"].astext == j.c.job_type,
            ),
            sa.or_(
                j.c.payload["script_code"].astext.is_(None),
                j.c.payload["script_code"].astext == m.projects.c.script_code,
            ),
            sa.or_(
                j.c.resource_profile["result_schema_version"].astext.is_(None),
                j.c.resource_profile["result_schema_version"].astext == "1",
            ),
            running < owner_cap,
        )
        .order_by(effective.desc(), running, last_served, j.c.run_after, j.c.created_at, j.c.id)
    )
    if job_id is not None:
        query = query.where(j.c.id == job_id)
    return query.limit(32)


async def candidates(connection, types, versions, memory, job_id=None):
    await connection.execute(sa.select(sa.func.pg_advisory_xact_lock(CLAIM_LOCK)))
    config = await policy(connection)
    running = await connection.scalar(
        sa.select(sa.func.count())
        .select_from(m.jobs)
        .where(m.jobs.c.state.in_(["running", "cancel_requested"]))
    )
    if running >= config.global_running:
        return []
    parsers: dict[str, type[BaseModel]] = dict(
        asset_verify=AssetVerifyJob,
        analyze_samples=AnalyzeSamplesJob,
        compile_export=CompileExportJob,
        privacy_maintenance=PrivacyMaintenanceJob,
    )
    supported = []
    query = (
        claim_query(types, versions, memory, config, job_id)
        .limit(None)
        .execution_options(yield_per=32)
    )
    async with connection.stream(query) as stream:
        async for row in stream.mappings():
            try:
                parser = parsers.get(row["job_type"])
                if parser is None:
                    continue
                payload = (
                    parse_analysis_payload(row["payload"])
                    if row["job_type"] == "analyze_samples"
                    else parser.model_validate(row["payload"])
                )
                if getattr(payload, "schema_version", None) != row["payload_schema_version"]:
                    continue
            except ValidationError:
                continue
            supported.append(row)
            if len(supported) == 32:
                break
    return supported


async def reserve(connection, owner, job, amounts, now):
    from datetime import timedelta
    from uuid import uuid4

    from app.core.errors import ApiError

    if not amounts:
        return
    if any(not isinstance(v, int) or v <= 0 for v in amounts.values()):
        raise ValueError("invalid_reservation")
    await configured_balances(connection, owner, amounts, now)
    from app.modules.jobs.service import lock_balances

    await lock_balances(connection, owner)
    balances = list(
        (
            await connection.execute(
                sa.select(m.quota_balances)
                .where(
                    m.quota_balances.c.resource.in_(amounts),
                    sa.or_(
                        m.quota_balances.c.user_id == owner, m.quota_balances.c.scope == "global"
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
    for resource in amounts:
        if {b["scope"] for b in balances if b["resource"] == resource} != {"user", "global"}:
            raise ApiError("quota_unconfigured", 503)
    for balance in balances:
        amount = amounts[balance["resource"]]
        if balance["committed"] + balance["reserved"] + amount > balance["limit_amount"]:
            raise ApiError("quota_exceeded", 429)
    for balance in balances:
        amount = amounts[balance["resource"]]
        await connection.execute(
            m.quota_balances.update()
            .where(m.quota_balances.c.id == balance["id"])
            .values(reserved=m.quota_balances.c.reserved + amount)
        )
        await connection.execute(
            m.quota_reservations.insert().values(
                id=uuid4(),
                balance_id=balance["id"],
                user_id=owner,
                job_id=job,
                resource=balance["resource"],
                amount=amount,
                status="reserved",
                expires_at=now + timedelta(days=1),
            )
        )


async def configured_balances(connection, owner, resources, now):
    """Optional versioned tier provisioning; absent configuration never invents a quota."""
    from datetime import date
    from uuid import uuid4

    from sqlalchemy.dialects.postgresql import insert

    config = await policy(connection)
    tiers = await connection.scalar(
        sa.select(m.system_settings.c.value).where(
            m.system_settings.c.key == f"quota_tiers.v{config.version}"
        )
    )
    if tiers is None:
        return  # Retained explicitly provisioned balance records are still supported.
    from app.core.errors import ApiError
    from app.modules.jobs.quota_config import QuotaConfiguration

    try:
        tiers = QuotaConfiguration.model_validate(tiers)
        if tiers.version != config.version:
            raise ValueError("quota_configuration_version_mismatch")
    except (ValueError, ValidationError):
        raise ApiError("quota_unconfigured", 503) from None
    tier = await connection.scalar(sa.select(m.users.c.tier).where(m.users.c.id == owner))
    for scope, limits in (
        ("user", tiers.tiers.get(tier, {})),
        ("global", tiers.global_limits),
    ):
        for resource in sorted(resources):
            rule = limits.get(resource)
            if not rule:
                continue
            window = rule.window_kind
            limit = rule.limit_amount
            start = (
                date(1970, 1, 1)
                if window == "lifetime"
                else now.date().replace(day=1)
                if window == "month"
                else now.date()
            )
            await connection.execute(
                insert(m.quota_balances)
                .values(
                    id=uuid4(),
                    scope=scope,
                    user_id=owner if scope == "user" else None,
                    resource=resource,
                    window_kind=window,
                    window_start=start,
                    limit_amount=limit,
                )
                .on_conflict_do_nothing()
            )
