import json
import logging
import math

import redis.asyncio as redis

from llm_wiki.application.ports.search.vector_search import CacheServicePort
from llm_wiki.config import settings

logger = logging.getLogger(__name__)

_SEMANTIC_PREFIX = "sem:"


class RedisCacheAdapter(CacheServicePort):
    """Redis-backed cache adapter with optional semantic (embedding) storage.

    Semantic cache stores question embeddings as Redis hash fields so
    ``semantic_get`` can scan with cosine similarity without a vector
    database on the cache layer.  This is O(N) in the number of cached
    questions — acceptable up to ~10k entries with 1024-d vectors.
    """

    def __init__(self, client: redis.Redis | None = None):
        self._client = client

    async def _ensure_client(self):
        if self._client is None:
            self._client = redis.from_url(settings.redis_url, decode_responses=True)
        return self._client

    async def get(self, key: str) -> str | None:
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

    # ── semantic cache ────────────────────────────────────────────────────

    async def semantic_get(self, embedding: list[float], threshold: float = 0.95) -> str | None:
        """Scan all stored question embeddings and return the best match above *threshold*."""
        try:
            client = await self._ensure_client()
            keys = await client.keys(f"{_SEMANTIC_PREFIX}*")
            if not keys:
                return None

            best_score = -1.0
            best_value: str | None = None
            emb_norm = _l2_norm(embedding)

            for key in keys:
                raw = await client.hgetall(key)
                if not raw or "emb" not in raw:
                    continue
                try:
                    stored_emb = json.loads(raw["emb"])
                except (json.JSONDecodeError, TypeError):
                    continue
                stored_norm = float(raw.get("norm", 0))
                dot = _dot_product(embedding, stored_emb)
                norm_product = emb_norm * stored_norm
                if norm_product == 0:
                    continue
                similarity = dot / norm_product
                if similarity > best_score:
                    best_score = similarity
                    best_value = raw.get("value", None)

            if best_score >= threshold and best_value is not None:
                logger.debug(
                    "Semantic cache hit: similarity=%.4f threshold=%.2f", best_score, threshold
                )
                return best_value
            return None
        except Exception:
            logger.debug("Redis semantic cache unavailable", exc_info=True)
            return None

    async def semantic_set(
        self, key: str, embedding: list[float], value: str, ttl: int = 3600
    ) -> None:
        """Store the embedding for this key so ``semantic_get`` can match it."""
        try:
            client = await self._ensure_client()
            sem_key = f"{_SEMANTIC_PREFIX}{key}"
            data = {
                "emb": json.dumps(embedding),
                "norm": _l2_norm(embedding),
                "value": value,
            }
            await client.hset(sem_key, mapping=data)
            await client.expire(sem_key, ttl)
        except Exception:
            logger.debug("Redis semantic cache set failed", exc_info=True)


def _dot_product(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def _l2_norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))
