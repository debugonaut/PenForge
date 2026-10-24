"""Restricted, content-free gauges over retained operational records.

These are snapshots, not ever-increasing counters: retention/deletion can reduce them.
Unknown enum values collapse into one fixed label and never escape into the scrape.
"""

import math

import sqlalchemy as sa

JOB_TYPES = {"asset_verify", "analyze_samples", "compile_export", "privacy_maintenance"}
STATES = {"queued", "running", "cancel_requested", "cancelled", "succeeded", "failed"}
RESOURCES = {"storage_bytes", "compute_seconds", "exports", "generation_candidates"}


def label(value, allowed):
    return value if value in allowed else "other"


async def collect(connection) -> list[str]:
    lines = ["penforge_scrape_db_ok 1"]
    jobs = (
        await connection.execute(
            sa.text(
                "SELECT job_type, state, count(*), COALESCE(sum(GREATEST(attempts-1,0)),0) "
                "FROM jobs GROUP BY job_type,state"
            )
        )
    ).all()
    # Aggregate again after collapsing unknown values to keep a valid exposition.
    grouped: dict[tuple[str, str], list[int]] = {}
    for kind, state, count, retries in jobs:
        key = (label(kind, JOB_TYPES), label(state, STATES))
        totals = grouped.setdefault(key, [0, 0])
        totals[0] += int(count)
        totals[1] += int(retries)
    for (kind, state), (count, retries) in sorted(grouped.items()):
        tags = f'type="{kind}",state="{state}"'
        lines += [
            f"penforge_queue_depth{{{tags}}} {count}",
            f"penforge_retained_job_retries{{{tags}}} {retries}",
        ]
    for name, query in {
        "oldest_queued_seconds": "SELECT COALESCE(MAX(EXTRACT(EPOCH FROM (now()-run_after))),0) FROM jobs WHERE state='queued' AND run_after<=now()",
        "expired_leases": "SELECT count(*) FROM jobs WHERE state IN ('running','cancel_requested') AND lease_expires_at<=now()",
        "lease_expiry_events_24h": "SELECT count(*) FROM job_events WHERE created_at>=now()-interval '24 hours' AND metadata->>'error_code' IN ('lease_expired','lease_expired_max_attempts')",
        "stored_asset_bytes": "SELECT COALESCE(sum(size_bytes),0) FROM assets WHERE state='ready' AND duplicate_of IS NULL",
        "failed_deletions": "SELECT count(*) FROM deletion_requests WHERE state='failed'",
        "oldest_pending_deletion_seconds": "SELECT COALESCE(MAX(EXTRACT(EPOCH FROM (now()-requested_at))),0) FROM deletion_requests WHERE state IN ('requested','in_progress')",
        "consent_withdrawals_pending": "SELECT count(*) FROM dataset_contributions d JOIN training_consents c ON c.id=d.consent_id WHERE c.withdrawn_at IS NOT NULL AND d.withdrawn_at IS NULL",
    }.items():
        value = float(await connection.scalar(sa.text(query)) or 0)
        lines.append(f"penforge_{name} {max(0, value):.3f}")
    quotas = (
        await connection.execute(
            sa.text(
                "SELECT scope,resource,sum(reserved),sum(committed),sum(limit_amount) FROM quota_balances "
                "WHERE window_kind='lifetime' OR (window_kind='day' AND window_start=(now() AT TIME ZONE 'UTC')::date) "
                "OR (window_kind='month' AND window_start=date_trunc('month',now() AT TIME ZONE 'UTC')::date) "
                "GROUP BY scope,resource"
            )
        )
    ).all()
    for scope, resource, reserved, committed, limit in quotas:
        # These are schema-constrained values; ignore unknown future resources until supported.
        if scope not in {"user", "global"} or resource not in RESOURCES:
            continue
        tags = f'scope="{scope}",resource="{resource}"'
        for suffix, value in (("reserved", reserved), ("committed", committed), ("limit", limit)):
            lines.append(f"penforge_quota_{resource}_{suffix}{{{tags}}} {int(value)}")
    for format, status, count in (
        await connection.execute(
            sa.text(
                "SELECT format,validation_status,count(*) FROM font_exports "
                "WHERE validation_status IS NOT NULL GROUP BY format,validation_status"
            )
        )
    ).all():
        if format in {"ttf", "otf", "woff2"} and status in {
            "passed",
            "passed_with_warnings",
            "failed",
        }:
            lines.append(
                f'penforge_retained_validations{{format="{format}",status="{status}"}} {count}'
            )
    # Only numeric scalar fields are cast; no report strings, paths or IDs are exposed.
    for kind, seconds in (
        await connection.execute(
            sa.text(
                "SELECT job_type,MAX(CASE WHEN jsonb_typeof(usage->'wall_seconds')='number' "
                "THEN (usage->>'wall_seconds')::double precision ELSE NULL END) FROM jobs GROUP BY job_type"
            )
        )
    ).all():
        if kind in JOB_TYPES and seconds is not None and math.isfinite(seconds) and seconds >= 0:
            lines.append(f'penforge_retained_job_wall_seconds_max{{type="{kind}"}} {seconds}')
    for field in ("compile_seconds", "validation_seconds", "wall_seconds", "peak_rss_bytes"):
        value = await connection.scalar(
            sa.text(
                "SELECT MAX(CASE WHEN jsonb_typeof(validation_report->'resources'->:field)='number' "
                "THEN (validation_report->'resources'->>:field)::double precision ELSE NULL END) "
                "FROM font_exports"
            ),
            {"field": field},
        )
        if value is not None and math.isfinite(value) and value >= 0:
            lines.append(f"penforge_retained_font_{field}_max {value}")
    return lines
