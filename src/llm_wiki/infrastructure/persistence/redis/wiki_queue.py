"""Redis queue for wiki integration jobs.

Stage-1 workers (cpu-worker) push {source_item_id} after classify+embed.
Wiki consumers pop and execute wiki_integrate + section_embed.

Dedup: items tracked in wiki:queue_dedup_set (Redis SET). push_wiki_job skips
if already in set. pop_wiki_job removes from set on success. Prevents the
massive queue bloat that caused 267K duplicates from 1003 unique items.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from redis.asyncio import Redis

from llm_wiki.config import settings

logger = logging.getLogger(__name__)

WIKI_QUEUE_KEY = "wiki:queue"
WIKI_DEDUP_KEY = "wiki:queue_dedup_set"

_redis: Redis | None = None


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=10.0,
            socket_connect_timeout=5.0,
            retry_on_timeout=True,
        )
        await _redis.ping()
        logger.info("Redis connected: %s", settings.redis_url)
    return _redis


async def push_wiki_job(source_item_id: UUID) -> int:
    """Push a source_item_id onto the wiki queue for async processing.

    Uses a Redis SET for dedup — if item already in queue (tracked by set),
    the push is silently skipped. Returns the queue length after push.
    """
    r = await get_redis()
    item_str = str(source_item_id)
    added = await r.sadd(WIKI_DEDUP_KEY, item_str)
    if not added:
        logger.debug("Skipped duplicate push for %s (already in queue)", source_item_id)
        return await r.llen(WIKI_QUEUE_KEY)
    length = await r.rpush(WIKI_QUEUE_KEY, item_str)
    logger.debug("Pushed %s to wiki queue (length=%d)", source_item_id, length)
    return length


async def pop_wiki_job(timeout: float = 30.0) -> UUID | None:
    """Blocking pop from wiki queue with timeout.

    Returns the source_item_id UUID, or None on timeout.
    Removes item from dedup set so it can be re-queued later if needed.
    """
    r = await get_redis()
    result = await r.blpop(WIKI_QUEUE_KEY, timeout=timeout)
    if result is None:
        return None
    _, item_id_str = result
    await r.srem(WIKI_DEDUP_KEY, item_id_str)
    return UUID(item_id_str)


async def queue_length() -> int:
    r = await get_redis()
    return await r.llen(WIKI_QUEUE_KEY)


async def cleanup_orphaned_dedup() -> int:
    """Remove dedup set entries that have no matching item in queue (orphan cleanup).

    Returns number of entries removed.
    """
    r = await get_redis()
    dedup_members = await r.smembers(WIKI_DEDUP_KEY)
    queue_items = set(await r.lrange(WIKI_QUEUE_KEY, 0, -1))
    orphans = dedup_members - queue_items
    if orphans:
        await r.srem(WIKI_DEDUP_KEY, *orphans)
        logger.info("Cleaned up %d orphaned dedup entries", len(orphans))
    return len(orphans)


async def clear_duplicates() -> int:
    """Emergency cleanup: remove all duplicate entries from the queue.

    Scans the queue and keeps only the first occurrence of each item_id.
    Returns the number of duplicates removed.
    """
    r = await get_redis()
    items = await r.lrange(WIKI_QUEUE_KEY, 0, -1)
    seen = set()
    unique = []
    removed = 0
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
        else:
            removed += 1
    if removed:
        await r.delete(WIKI_QUEUE_KEY)
        await r.delete(WIKI_DEDUP_KEY)
        if unique:
            await r.rpush(WIKI_QUEUE_KEY, *unique)
            await r.sadd(WIKI_DEDUP_KEY, *unique)
        logger.info("Cleared %d duplicates, %d unique items remaining in queue", removed, len(unique))
    return removed


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Redis disconnected")
