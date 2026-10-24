"""Registry/storage bridge. Workers can verify artifacts, never approve them."""

import asyncio

from app.modules.models.service import ModelsService

from packages.ml_core.registry import ModelCache


class ModelRegistry:
    def __init__(self, engine, storage, **cache_bounds):
        self.service, self.storage = ModelsService(engine), storage
        self.records: dict[str, dict] = {}
        self.cache = ModelCache(
            lambda key: self.records[key],
            lambda key, size: storage.read_object(key, max_bytes=size),
            **cache_bounds,
        )
        self.lock = asyncio.Lock()

    async def get(self, model_id, **requirements):
        async with self.lock:
            try:
                self.records[model_id] = await self.service.resolve(model_id)
                return await asyncio.to_thread(self.cache.get, model_id, **requirements)
            finally:
                self.records.pop(model_id, None)

    def evict(self, model_id=None):
        self.cache.evict(model_id)
