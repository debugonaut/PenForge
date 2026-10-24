"""Alembic operates against DATABASE_URL; it never imports API runtime services."""

import os

from alembic import context
from app.db.models import metadata
from app.db.session import normalize_database_url
from sqlalchemy import create_engine, pool


def run_migrations_offline() -> None:
    url = normalize_database_url(os.environ["DATABASE_URL"])
    context.configure(
        url=url,
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    existing = context.config.attributes.get("connection")
    if existing is not None:
        context.configure(
            connection=existing,
            target_metadata=metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(
        normalize_database_url(os.environ["DATABASE_URL"]), poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
