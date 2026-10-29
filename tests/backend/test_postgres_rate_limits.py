"""Shared admission windows: deterministic logic without a database, plus real PostgreSQL.

Hosted API instances share one budget, so the integration case proves two separate
limiter objects over the same schema cannot each spend the same allowance.
"""

from __future__ import annotations

import asyncio
import math
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.core.ratelimit import PostgresRateLimiter
from app.db.session import normalize_database_url
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[2]


class Result:
    def __init__(self, used: int):
        self._used = used

    def scalar_one(self) -> int:
        return self._used


class RecordingConnection:
    def __init__(self, used: int):
        self.used = used
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, parameters):
        self.calls.append((str(statement), dict(parameters)))
        if "RETURNING used" in str(statement):
            return Result(self.used)
        return Result(0)


class RecordingTransaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *exc):
        return False


class FakeEngine:
    """Yields each configured ``used`` value once so window boundaries can be observed."""

    def __init__(self, used_values: list[int]):
        self.used_values = list(used_values)
        self.connections: list[RecordingConnection] = []

    def begin(self):
        connection = RecordingConnection(self.used_values.pop(0))
        self.connections.append(connection)
        return RecordingTransaction(connection)


NOW = 1_700_000_000.0


def test_window_is_epoch_aligned_and_reports_remaining():
    engine = FakeEngine([1])
    limiter = PostgresRateLimiter(engine, clock=lambda: NOW, cleanup_probability=0.0)  # type: ignore[arg-type]
    result = asyncio.run(limiter.consume("identity:claim:owner", limit=60, window_seconds=60))
    assert result.allowed and result.limit == 60 and result.remaining == 59
    expected_reset = max(1, math.ceil((math.floor(NOW / 60) + 1) * 60 - NOW))
    assert result.reset_after_seconds == expected_reset
    statement, parameters = engine.connections[0].calls[0]
    assert "ON CONFLICT" in statement and "RETURNING used" in statement
    assert parameters["key"] == "identity:claim:owner"
    assert parameters["limit"] == 60 and parameters["window_seconds"] == 60
    headers = result.headers()
    assert headers["RateLimit-Limit"] == "60" and "RateLimit-Remaining" in headers


def test_exhausted_budget_is_denied_with_retry_headers():
    limiter = PostgresRateLimiter(FakeEngine([6]), clock=lambda: NOW, cleanup_probability=0.0)  # type: ignore[arg-type]
    denied = asyncio.run(limiter.consume("abuse:ip:digest", limit=5, window_seconds=3600))
    assert not denied.allowed and denied.remaining == 0
    assert denied.headers()["Retry-After"] == str(denied.reset_after_seconds)
    for bad in ("", "x" * 257):
        with pytest.raises(ValueError):
            asyncio.run(limiter.consume(bad, limit=5, window_seconds=60))
    for bad in (0, -1):
        with pytest.raises(ValueError):
            asyncio.run(limiter.consume("k", limit=bad, window_seconds=60))


def test_unbounded_clock_is_refused():
    limiter = PostgresRateLimiter(FakeEngine([1]), clock=lambda: math.inf, cleanup_probability=0.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        asyncio.run(limiter.consume("k", limit=1, window_seconds=60))


def migrate(engine: Engine, direction: str) -> None:
    config = Config(str(ROOT / "infrastructure/migrations/alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        getattr(command, direction)(config, "head")


@pytest.fixture(scope="module")
def database() -> Iterator[tuple[Engine, str]]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL acceptance")
    admin = sa.create_engine(normalize_database_url(database_url))
    schema = "rate_limit_test_" + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = sa.create_engine(
        normalize_database_url(database_url), connect_args={"options": f"-csearch_path={schema}"}
    )
    try:
        migrate(engine, "upgrade")
        yield engine, schema
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        engine.dispose()
        admin.dispose()


@pytest.mark.integration
def test_two_api_instances_share_one_admission_budget(database: tuple[Engine, str]):
    engine, schema = database
    url = normalize_database_url(engine.url.render_as_string(hide_password=False))
    options = f"-csearch_path={schema}"
    first = create_async_engine(url, connect_args={"options": options}, poolclass=NullPool)
    second = create_async_engine(url, connect_args={"options": options}, poolclass=NullPool)

    async def scenario() -> list[bool]:
        limiter_a = PostgresRateLimiter(first, clock=lambda: NOW, cleanup_probability=0.0)
        limiter_b = PostgresRateLimiter(second, clock=lambda: NOW, cleanup_probability=0.0)
        shared = [
            await limiter_a.consume("identity:claim:same", limit=2, window_seconds=60),
            await limiter_b.consume("identity:claim:same", limit=2, window_seconds=60),
            await limiter_b.consume("identity:claim:same", limit=2, window_seconds=60),
        ]
        assert [item.allowed for item in shared] == [True, True, False]
        assert [item.remaining for item in shared] == [1, 0, 0]
        # The next aligned window starts a fresh allowance for the same key.
        later = PostgresRateLimiter(second, clock=lambda: NOW + 60, cleanup_probability=0.0)
        assert (await later.consume("identity:claim:same", limit=2, window_seconds=60)).allowed
        return [item.allowed for item in shared]

    try:
        assert asyncio.run(scenario()) == [True, True, False]
    finally:
        asyncio.run(first.dispose())
        asyncio.run(second.dispose())
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT key, limit_amount, window_seconds, used FROM rate_limit_windows ORDER BY used"
            )
        ).fetchall()
    assert rows, "shared windows must be recorded in PostgreSQL"
    assert {(row.key, row.limit_amount, row.window_seconds) for row in rows} >= {
        ("identity:claim:same", 2, 60)
    }
    assert max(row.used for row in rows) >= 3
