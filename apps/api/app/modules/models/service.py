"""Controlled registry resolution only; no public registration/promotion endpoint."""

from uuid import UUID

from app.core.errors import ApiError
from app.modules.models.repository import ModelsRepository


class ModelsService:
    def __init__(self, engine):
        self.engine = engine

    async def resolve(self, model_id):
        async with self.engine.connect() as con:
            row = await ModelsRepository(con).get(UUID(model_id))
            if row is None:
                raise ApiError("not_found", 404)
            support = row["provenance"].get("support", {})
            return {
                "id": str(row["id"]),
                "task": row["task"],
                "format": row["format"],
                "artifact_key": row["artifact_key"],
                "sha256": row["sha256"].hex(),
                "size_bytes": row["size_bytes"],
                "input_schema": row["input_schema"],
                "output_schema": row["output_schema"],
                "scripts": support.get("scripts", []),
                "styles": support.get("styles", []),
                "input_classes": support.get("input_classes", []),
                "compatible_worker": row["compatible_worker"],
                "license": row["license"],
                "provenance": row["provenance"],
                "status": row["status"],
                "resource_profile": row["resource_profile"],
            }
