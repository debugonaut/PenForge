"""Portable psycopg connection factories without import-time connections."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def normalize_database_url(url: str) -> str:
    """Use psycopg 3 for both sync workers/migrations and async API connections."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("PostgreSQL with psycopg is required")
    return url


def make_engine(url: str, *, pool_size: int = 5) -> Engine:
    return create_engine(normalize_database_url(url), pool_size=pool_size, pool_pre_ping=True)


def make_async_engine(url: str, *, pool_size: int = 5) -> AsyncEngine:
    return create_async_engine(normalize_database_url(url), pool_size=pool_size, pool_pre_ping=True)
