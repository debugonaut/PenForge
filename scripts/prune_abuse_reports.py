"""Operator-only 90-day report retention. Default is a content-free dry run."""

import argparse
import asyncio
import json

from app.core.config import Settings
from app.db.session import make_async_engine
from app.modules.accountability.service import AccountabilityService
from app.modules.identity.service import IdentityService


async def run(apply: bool) -> None:
    settings = Settings()
    engine = make_async_engine(settings.database_url.get_secret_value())
    try:
        count = await AccountabilityService(IdentityService(engine), settings).prune_reports(
            apply=apply
        )
        print(json.dumps({"applied": apply, "expired_reports": count, "retention_days": 90}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete reports older than 90 days")
    asyncio.run(run(parser.parse_args().apply))
