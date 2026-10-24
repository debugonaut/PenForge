"""Validate quota configuration; --apply requires an explicit operator database URL.

Existing configurations are never silently replaced: use a new reviewed version
with the corresponding queue-policy version, or a separately reviewed balance update.
"""

import argparse
import json
import os
from pathlib import Path

from app.db import models as m
from app.db.session import make_engine
from app.modules.jobs.quota_config import QuotaConfiguration
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert


def provision(connection, config: QuotaConfiguration):
    key = f"quota_tiers.v{config.version}"
    value = config.model_dump(mode="json", by_alias=True)
    connection.execute(
        insert(m.system_settings).values(key=key, value=value).on_conflict_do_nothing()
    )
    existing = connection.scalar(
        select(m.system_settings.c.value).where(m.system_settings.c.key == key)
    )
    if existing != value:
        raise ValueError("quota_version_already_has_different_limits")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = QuotaConfiguration.model_validate(json.loads(args.configuration.read_text()))
    if args.apply:
        if "DATABASE_URL" not in os.environ:
            raise SystemExit("DATABASE_URL must explicitly select the operator database")
        engine = make_engine(os.environ["DATABASE_URL"])
        try:
            with engine.begin() as connection:
                provision(connection, config)
        finally:
            engine.dispose()
    print(
        "Quota configuration provisioned."
        if args.apply
        else "Quota configuration valid; no database changed."
    )


if __name__ == "__main__":
    main()
