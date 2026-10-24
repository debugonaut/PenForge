"""Separate management application; never mounted on the public API."""

import asyncio
import hmac

from fastapi import FastAPI, Request
from sqlalchemy import text

from app.core.config import Settings
from app.db.revision import EXPECTED_MIGRATION_HEAD
from app.db.session import make_async_engine
from app.modules.health.schemas import HealthResponse


def create_internal_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/ready")
    async def ready(request: Request):
        from starlette.responses import JSONResponse

        expected = config.readiness_token
        supplied = request.headers.get("authorization", "")
        if expected is None or not hmac.compare_digest(
            supplied.encode(), ("Bearer " + expected.get_secret_value()).encode()
        ):
            return JSONResponse({"status": "unavailable"}, status_code=404)
        engine = None
        try:
            engine = make_async_engine(config.database_url.get_secret_value(), pool_size=1)
            async with asyncio.timeout(3):
                async with engine.connect() as connection:
                    head = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if head != EXPECTED_MIGRATION_HEAD:
                return JSONResponse({"status": "unavailable"}, status_code=503)
            return HealthResponse().model_dump()
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        finally:
            if engine:
                await engine.dispose()

    return app
