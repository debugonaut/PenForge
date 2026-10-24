import sqlalchemy as sa

from app.db import models as m


class ModelsRepository:
    def __init__(self, connection):
        self.connection = connection

    async def get(self, model_id):
        return (
            (
                await self.connection.execute(
                    sa.select(m.model_versions).where(m.model_versions.c.id == model_id)
                )
            )
            .mappings()
            .one_or_none()
        )
