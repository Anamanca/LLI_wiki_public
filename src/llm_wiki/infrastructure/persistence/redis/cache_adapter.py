import json
import logging
from typing import Optional

import redis.asyncio as redis

from llm_wiki.application.ports.search.vector_search import CacheServicePort
from llm_wiki.config import settings

logger = logging.getLogger(__name__)


class RedisCacheAdapter(CacheServicePort):
    def __init__(self, client: redis.Redis | None = None):
        self._client = client

    async def _ensure_client(self):
        if self._client is None:
            self._client = redis.from_url(settings.redis_url, decode_responses=True)
        return self._client

    async def get(self, key: str) -> Optional[str]:
        try:
            client = await self._ensure_client()
            return await client.get(key)
        except Exception:
            logger.debug("Redis cache unavailable", exc_info=True)
            return None

    async def set(self, key: str, value: str, ttl: int = 3600) -> None:
        try:
            client = await self._ensure_client()
            await client.setex(key, ttl, value)
        except Exception:
            logger.debug("Redis cache set failed", exc_info=True)

    async def delete(self, key: str) -> None:
        try:
            client = await self._ensure_client()
            await client.delete(key)
        except Exception:
            logger.debug("Redis cache delete failed", exc_info=True)
