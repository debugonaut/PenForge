"""Phase 11 PostgreSQL benchmark and restore drill.

Measures the real migrated schema with the application's actual claim query
(``app.modules.jobs.repository.candidates``) plus a lease update in the same
transaction. Never labels a local run as a hosted result: the JSON records
``provider`` and ``region`` for whatever DSN was used.

Usage:
    .venv/bin/python scripts/phase11_pg_benchmark.py --jobs 60
    .venv/bin/python scripts/phase11_pg_benchmark.py --dsn postgresql://... --provider neon --region ap-southeast-1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/api"), str(ROOT / "packages/contracts"), str(ROOT)]

from app.core.errors import ApiError  # noqa: E402
from app.db import models as m  # noqa: E402
from app.db.revision import EXPECTED_MIGRATION_HEAD  # noqa: E402
from app.db.session import normalize_database_url  # noqa: E402
from app.modules.jobs import repository  # noqa: E402

SCHEMA_PREFIX = "phase11_bench_"
OUTPUT_DIR = ROOT / "research/evaluation/benchmarks"


def stats(values: list[float], *, unit: str = "ms") -> dict:
    if not values:
        return {"count": 0, "unit": unit}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "unit": unit,
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "max": round(ordered[-1], 3),
        "mean": round(statistics.fmean(ordered), 3),
    }


def search_options(schema: str) -> dict:
    return {"options": f"-csearch_path={schema}"}


def migrate(dsn: str, schema: str) -> None:
    engine = sa.create_engine(dsn, connect_args=search_options(schema))
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        with engine.begin() as connection:
            config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
            config.attributes["connection"] = connection
            config.set_main_option("script_location", str(ROOT / "infrastructure/migrations"))
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def seed(dsn: str, schema: str, owners: int, per_owner: int) -> dict:
    now = datetime.now(UTC)
    payload_tail = {
        "schema_version": 1,
        "job_type": "compile_export",
        "formats": ["ttf"],
    }
    users, projects, jobs = [], [], []
    for _ in range(owners):
        user_id, project_id = uuid4(), uuid4()
        users.append(dict(id=user_id, firebase_uid=uuid4().hex))
        projects.append(
            dict(
                id=project_id,
                owner_user_id=user_id,
                name="Phase 11 synthetic benchmark",
                script_code="Latn",
                style_controls={},
            )
        )
        for _ in range(per_owner):
            jobs.append(
                dict(
                    id=uuid4(),
                    project_id=project_id,
                    owner_user_id=user_id,
                    job_type="compile_export",
                    payload={
                        **payload_tail,
                        "export_ids": [str(uuid4())],
                        "snapshot_id": str(uuid4()),
                    },
                    payload_schema_version=1,
                    state="queued",
                    run_after=now - timedelta(seconds=1),
                    resource_profile={"mem_mb": 768, "wall_seconds": 300},
                )
            )
    engine = sa.create_engine(dsn, connect_args=search_options(schema))
    try:
        with engine.begin() as connection:
            # An explicit benchmark policy so concurrency, not the default 4-job global
            # ceiling, is what the claim runs measure. Production defaults are unchanged.
            connection.execute(
                m.system_settings.insert().values(
                    key="queue_policy.v1",
                    value={
                        "version": 1,
                        "global_running": 64,
                        "owner_running": 8,
                        "aging_seconds": 300,
                        "degradation_level": 0,
                    },
                )
            )
            connection.execute(m.users.insert(), users)
            connection.execute(m.projects.insert(), projects)
            connection.execute(m.jobs.insert(), jobs)
    finally:
        engine.dispose()
    return {"owners": len(users), "jobs": len(jobs)}


def reset_jobs(dsn: str, schema: str) -> None:
    engine = sa.create_engine(dsn, connect_args=search_options(schema))
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE jobs SET state='queued', lease_holder=NULL, lease_expires_at=NULL, "
                    "attempts=0, last_heartbeat_at=NULL, started_at=NULL, finished_at=NULL"
                )
            )
    finally:
        engine.dispose()


def cold_connect(dsn: str, schema: str, samples: int) -> dict:
    timings = []
    for _ in range(samples):
        started = time.perf_counter()
        engine = sa.create_engine(dsn, connect_args=search_options(schema), pool_size=1)
        try:
            with engine.connect() as connection:
                connection.execute(sa.text("SELECT 1")).scalar()
        finally:
            engine.dispose()
        timings.append((time.perf_counter() - started) * 1000)
    return stats(timings)


async def pooled_queries(dsn: str, schema: str, concurrency: int, each: int) -> dict:
    engine = create_async_engine(
        dsn, connect_args=search_options(schema), pool_size=max(4, concurrency)
    )
    timings: list[float] = []

    async def worker() -> None:
        for _ in range(each):
            started = time.perf_counter()
            async with engine.connect() as connection:
                (await connection.execute(sa.text("SELECT 1"))).scalar()
            timings.append((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    try:
        await asyncio.gather(*(worker() for _ in range(concurrency)))
    finally:
        await engine.dispose()
    wall = time.perf_counter() - started
    result = stats(timings)
    result["throughput_per_second"] = round(len(timings) / wall, 1) if wall else 0
    result["concurrency"] = concurrency
    return result


async def claims(dsn: str, schema: str, concurrency: int, attempts: int) -> dict:
    """Discovery through the real repository query, then a fenced lease update."""
    engine = create_async_engine(
        dsn, connect_args=search_options(schema), pool_size=max(4, concurrency)
    )
    timings: list[float] = []
    successful: list[float] = []
    empty: list[float] = []
    claimed = 0

    async def worker(name: str) -> None:
        nonlocal claimed
        for _ in range(attempts):
            started = time.perf_counter()
            found = False
            async with engine.begin() as connection:
                rows = await repository.candidates(connection, ("compile_export",), (1,), 768)
                if rows:
                    row = rows[0]
                    await connection.execute(
                        sa.update(m.jobs)
                        .where(m.jobs.c.id == row["id"], m.jobs.c.state == "queued")
                        .values(
                            state="running",
                            lease_holder=name,
                            lease_expires_at=sa.func.now() + sa.text("interval '60 seconds'"),
                            attempts=m.jobs.c.attempts + 1,
                        )
                    )
                    claimed += 1
                    found = True
            elapsed = (time.perf_counter() - started) * 1000
            timings.append(elapsed)
            (successful if found else empty).append(elapsed)

    started = time.perf_counter()
    try:
        await asyncio.gather(*(worker(f"bench-{i}") for i in range(concurrency)))
    finally:
        await engine.dispose()
    wall = time.perf_counter() - started
    result = stats(timings)
    result.update(
        concurrency=concurrency,
        attempts=concurrency * attempts,
        claimed=claimed,
        successful=stats(successful),
        empty=stats(empty),
        claims_per_second=round(claimed / wall, 1) if wall else 0,
    )
    return result


async def heartbeats(dsn: str, schema: str, concurrency: int, each: int) -> dict:
    engine = create_async_engine(dsn, connect_args=search_options(schema), pool_size=concurrency)
    async with engine.connect() as connection:
        ids = [
            tuple(row)
            for row in (
                await connection.execute(
                    sa.select(m.jobs.c.id, m.jobs.c.lease_holder, m.jobs.c.attempts).where(
                        m.jobs.c.state == "running"
                    )
                )
            ).all()
        ]
    if not ids:
        await engine.dispose()
        return {"count": 0, "unit": "ms", "note": "no leased jobs"}
    timings: list[float] = []

    async def worker(index: int) -> None:
        for step in range(each):
            job_id, holder, attempt = ids[(index * each + step) % len(ids)]
            started = time.perf_counter()
            async with engine.begin() as connection:
                updated = await connection.scalar(
                    sa.update(m.jobs)
                    .where(
                        m.jobs.c.id == job_id,
                        m.jobs.c.lease_holder == holder,
                        m.jobs.c.attempts == attempt,
                        m.jobs.c.state == "running",
                        m.jobs.c.lease_expires_at > sa.func.clock_timestamp(),
                    )
                    .values(
                        last_heartbeat_at=sa.func.clock_timestamp(),
                        progress=0.5,
                        lease_expires_at=sa.func.clock_timestamp()
                        + sa.text("interval '60 seconds'"),
                    )
                    .returning(m.jobs.c.id)
                )
                if updated is None:
                    raise RuntimeError("heartbeat_fence_rejected")
            timings.append((time.perf_counter() - started) * 1000)

    try:
        await asyncio.gather(*(worker(i) for i in range(concurrency)))
    finally:
        await engine.dispose()
    return stats(timings)


async def quota_reservations(dsn: str, schema: str, concurrency: int, cap: int) -> dict:
    """Use the application's actual reservation path and verify both balance ledgers."""
    engine = create_async_engine(dsn, connect_args=search_options(schema), pool_size=concurrency)
    async with engine.begin() as connection:
        owner, job = (
            await connection.execute(sa.select(m.jobs.c.owner_user_id, m.jobs.c.id).limit(1))
        ).one()
        await connection.execute(
            sa.delete(m.quota_balances).where(m.quota_balances.c.resource == "compute_seconds")
        )
        for scope, user_id in (("global", None), ("user", owner)):
            await connection.execute(
                m.quota_balances.insert().values(
                    id=uuid4(),
                    scope=scope,
                    user_id=user_id,
                    resource="compute_seconds",
                    window_kind="lifetime",
                    window_start=datetime(1970, 1, 1, tzinfo=UTC).date(),
                    limit_amount=cap,
                )
            )
    timings: list[float] = []
    rejected = 0

    async def worker() -> None:
        nonlocal rejected
        started = time.perf_counter()
        try:
            async with engine.begin() as connection:
                now = await connection.scalar(sa.select(sa.func.clock_timestamp()))
                await repository.reserve(connection, owner, job, {"compute_seconds": 60}, now)
        except ApiError as exc:
            if exc.code != "quota_exceeded":
                raise
            rejected += 1
        timings.append((time.perf_counter() - started) * 1000)

    try:
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        async with engine.connect() as connection:
            balances = list(
                (
                    await connection.execute(
                        sa.select(m.quota_balances.c.reserved).where(
                            m.quota_balances.c.resource == "compute_seconds"
                        )
                    )
                ).scalars()
            )
            reservations = await connection.scalar(
                sa.select(sa.func.count()).select_from(m.quota_reservations)
            )
        expected = min(concurrency, cap // 60) * 60
        if balances != [expected, expected] or reservations != 2 * (concurrency - rejected):
            raise RuntimeError("quota_ledger_mismatch")
    finally:
        await engine.dispose()
    result = stats(timings)
    result.update(
        concurrency=concurrency,
        rejected=rejected,
        cap=cap,
        reserved_per_scope=balances,
        reservations=reservations,
        path="repository.reserve",
    )
    return result


def restore_drill(dsn: str, schema: str, bin_path: Path) -> dict:
    """Time a dump/restore into a fresh database, then a representative query."""
    from sqlalchemy.engine import make_url

    url = make_url(dsn)
    source = url.set(drivername="postgresql").render_as_string(hide_password=False)
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    target = f"{url.database}_restore_{uuid4().hex[:8]}"
    target_url = url.set(drivername="postgresql", database=target).render_as_string(
        hide_password=False
    )
    dump = Path(tempfile.mkdtemp(prefix="penforge-restore-")) / "phase11.dump"
    psql, pg_dump, pg_restore, createdb = (
        bin_path / "psql",
        bin_path / "pg_dump",
        bin_path / "pg_restore",
        bin_path / "createdb",
    )
    started = time.perf_counter()
    subprocess.run(
        [str(pg_dump), f"--dbname={source}", "--format=custom", f"--file={dump}"], check=True
    )
    dump_seconds = time.perf_counter() - started
    started = time.perf_counter()
    subprocess.run([str(createdb), f"--maintenance-db={admin}", target], check=True)
    subprocess.run([str(pg_restore), f"--dbname={target_url}", str(dump)], check=True)
    restore_seconds = time.perf_counter() - started
    started = time.perf_counter()
    probe = subprocess.run(
        [
            str(psql),
            f"--dbname={target_url}",
            "--tuples-only",
            "--command",
            f'SELECT count(*) FROM "{schema}".jobs',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    usable_seconds = time.perf_counter() - started
    rounded = {"dump_seconds": round(dump_seconds, 3), "restore_seconds": round(restore_seconds, 3)}
    rounded["time_to_first_query_seconds"] = round(usable_seconds, 3)
    rounded["rows_after_restore"] = int(probe.stdout.strip() or 0)
    rounded["rpo_note"] = "measured dump point; restore loses everything written after it"
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as cleanup:
        cleanup.write(f'DROP DATABASE IF EXISTS "{target}";')
        cleanup.flush()
        subprocess.run(
            [str(psql), f"--dbname={admin}", "--file", cleanup.name],
            check=False,
            capture_output=True,
        )
    Path(cleanup.name).unlink(missing_ok=True)
    return rounded


async def measure(dsn: str, schema: str, samples: int) -> dict:
    results = {
        "cold_connect": cold_connect(dsn, schema, min(samples, 10)),
        "pooled_query": {},
        "claim": {},
        "heartbeat": {},
        "quota": {},
    }
    for concurrency in (1, 5, 20):
        results["pooled_query"][str(concurrency)] = await pooled_queries(
            dsn, schema, concurrency, each=max(20, samples)
        )
        reset_jobs(dsn, schema)
        results["claim"][str(concurrency)] = await claims(
            dsn, schema, concurrency, attempts=max(5, samples // 4)
        )
    results["heartbeat"] = await heartbeats(dsn, schema, 5, max(20, samples))
    for concurrency in (5, 20):
        results["quota"][str(concurrency)] = await quota_reservations(dsn, schema, concurrency, 600)
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="PostgreSQL URL to benchmark instead of a local cluster")
    parser.add_argument("--provider", default="local-pgserver")
    parser.add_argument("--region", default="local machine")
    parser.add_argument("--jobs", type=int, default=60, help="total seeded queued jobs")
    parser.add_argument("--owners", type=int, default=20)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--output")
    parser.add_argument("--skip-restore", action="store_true")
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.owners < 1 or args.samples < 1:
        parser.error("positive jobs, owners and samples required")
    schema = SCHEMA_PREFIX + uuid4().hex

    server = None
    temp = None
    if args.dsn:
        dsn = normalize_database_url(args.dsn)
    else:
        import pgserver

        temp = tempfile.mkdtemp(prefix="penforge-pg-")
        server = pgserver.get_server(temp, cleanup_mode="delete")
        dsn = server.get_uri().replace("postgresql://", "postgresql+psycopg://")
    try:
        migrate(dsn, schema)
        seeded = seed(dsn, schema, args.owners, max(1, args.jobs // args.owners))
        results = asyncio.run(measure(dsn, schema, args.samples))
        restored = {}
        if not args.skip_restore and not args.dsn:
            from pgserver._commands import POSTGRES_BIN_PATH

            restored = restore_drill(dsn, schema, Path(POSTGRES_BIN_PATH))
        metadata_engine = sa.create_engine(dsn, connect_args=search_options(schema))
        try:
            with metadata_engine.connect() as connection:
                version = connection.execute(sa.text("SHOW server_version")).scalar()
        finally:
            metadata_engine.dispose()
        report = {
            "schema": "phase11.pg-benchmark.v2",
            "date": datetime.now(UTC).isoformat(),
            "provider": args.provider,
            "region": args.region,
            "postgres_version": version,
            "migration_head": EXPECTED_MIGRATION_HEAD,
            "client": {"driver": "psycopg3", "python": platform.python_version()},
            "host": {
                "platform": platform.platform(),
                "cpu_count": platform.uname().processor or "",
            },
            "dataset": seeded,
            "results": results,
            "restore": restored,
            "notes": [
                "Claim timings use the application's repository.candidates query plus a fenced lease update.",
                "Local numbers are not hosted-provider results.",
                "Synthetic rows only; no handwriting, font or user content is present.",
            ],
        }
        target = (
            Path(args.output)
            if args.output
            else (OUTPUT_DIR / f"pg-{args.provider}-{datetime.now(UTC).strftime('%Y%m%d')}.json")
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(
            json.dumps(
                {"output": str(target), "provider": args.provider, "restore": restored}, indent=2
            )
        )
        return 0
    finally:
        cleanup_engine = sa.create_engine(dsn)
        try:
            with cleanup_engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            cleanup_engine.dispose()
        if server is not None:
            server.cleanup()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
