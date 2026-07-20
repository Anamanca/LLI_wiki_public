"""Periodic sweeper — recover jobs stuck in processing/wiki_processing state.

Runs every 2 minutes. Relies on worker heartbeat table to check if any worker
is still alive for a given job before resetting.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text, bindparam, update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.infrastructure.persistence.postgres.database import async_session_factory
from llm_wiki.infrastructure.persistence.postgres.models import SourceItem
from llm_wiki.infrastructure.persistence.redis.wiki_queue import (
    get_redis,
    WIKI_QUEUE_KEY,
    WIKI_DEDUP_KEY,
)

logger = logging.getLogger(__name__)

STALE_THRESHOLD_MINUTES = 15
STALE_WIKI_THRESHOLD_MINUTES = 60
STALE_CLASSIFIED_THRESHOLD_MINUTES = 120
SWEEP_INTERVAL_SECONDS = 120
WORKER_ALIVE_SECONDS = 300
_shutdown = False

_STALE_PROCESSING_SQL = text(
    """
    UPDATE source_items
    SET status = 'pending',
        error_message = 'stuck — recovered by stale sweeper',
        started_at = NULL
    WHERE status = 'processing'
      AND (
          started_at < NOW() - :threshold * INTERVAL '1 minute'
          OR started_at IS NULL
      )
      AND id NOT IN (
          SELECT current_job_id
          FROM worker_heartbeats
          WHERE current_job_id IS NOT NULL
            AND last_heartbeat > NOW() - :alive * INTERVAL '1 second'
      )
    """
).bindparams(
    bindparam("threshold", value=STALE_THRESHOLD_MINUTES),
    bindparam("alive", value=WORKER_ALIVE_SECONDS),
)

_RECOVER_WIKI_SQL = text(
    """
    UPDATE source_items
    SET status = 'classified',
        error_message = 'wiki stuck — recovered by stale sweeper',
        started_at = NULL
    WHERE status = 'wiki_processing'
      AND retry_count < :max_recovery_retries
      AND (
          started_at < NOW() - :threshold * INTERVAL '1 minute'
          OR started_at IS NULL
      )
      AND id NOT IN (
          SELECT current_job_id
          FROM worker_heartbeats
          WHERE current_job_id IS NOT NULL
            AND last_heartbeat > NOW() - :alive * INTERVAL '1 second'
      )
    RETURNING id
    """
).bindparams(
    bindparam("threshold", value=STALE_WIKI_THRESHOLD_MINUTES),
    bindparam("alive", value=WORKER_ALIVE_SECONDS),
    bindparam("max_recovery_retries", value=10),
)

_RECOVER_CLASSIFIED_SQL = text(
    """
    SELECT id
    FROM source_items
    WHERE status = 'classified'
      AND retry_count < :max_recovery_retries
    """
).bindparams(
    bindparam("max_recovery_retries", value=10),
)

_STALE_PENDING_TRANSCRIBE_SQL = text(
    """
    UPDATE source_items
    SET status = 'no_captions_t3_fail',
        error_message = 'stale pending_transcribe — no GPU worker available, reset to no_captions',
        started_at = NULL
    WHERE status = 'pending_transcribe'
      AND started_at IS NOT NULL
      AND started_at < NOW() - :threshold * INTERVAL '1 minute'
    """
).bindparams(
    bindparam("threshold", value=STALE_THRESHOLD_MINUTES),
)

_STALE_TRANSCRIBING_SQL = text(
    """
    UPDATE source_items
    SET status = 'pending_transcribe',
        error_message = 'transcribing stuck — recovered by stale sweeper',
        started_at = NULL
    WHERE status = 'transcribing'
      AND (
          started_at < NOW() - :threshold * INTERVAL '1 minute'
          OR started_at IS NULL
      )
      AND id NOT IN (
          SELECT current_job_id
          FROM worker_heartbeats
          WHERE current_job_id IS NOT NULL
            AND last_heartbeat > NOW() - :alive * INTERVAL '1 second'
      )
    """
).bindparams(
    bindparam("threshold", value=STALE_THRESHOLD_MINUTES),
    bindparam("alive", value=WORKER_ALIVE_SECONDS),
)


async def _requeue_classified(item_ids: list[str]) -> int:
    """Re-queue specific items to Redis wiki queue.

    Used for items recovered from wiki_processing zombie state and
    classified items that are not already in the queue.
    Deduplicates via Redis SET to prevent bloat — each item only queued once.
    Cleans up orphaned dedup entries first to prevent false blocking.
    """
    if not item_ids:
        return 0

    r = await get_redis()

    # Clean orphaned dedup entries — items in set but not in queue list
    dedup_members = await r.smembers(WIKI_DEDUP_KEY)
    queue_items = set(await r.lrange(WIKI_QUEUE_KEY, 0, -1))
    orphans = dedup_members - queue_items
    if orphans:
        await r.srem(WIKI_DEDUP_KEY, *orphans)
        logger.info("Cleaned up %d orphaned dedup entries before re-queue", len(orphans))

    count = 0
    for item_id in item_ids:
        added = await r.sadd(WIKI_DEDUP_KEY, item_id)
        if not added:
            continue
        await r.rpush(WIKI_QUEUE_KEY, item_id)
        count += 1

    if count > 0:
        logger.info(
            "Re-queued %d recovered wiki items (skipped %d duplicates)",
            count,
            len(item_ids) - count,
        )
    return count


async def _recover_classified_items() -> tuple[int, int]:
    """Find classified items not in the wiki queue and re-queue them.

    Increments retry_count on each re-queue. Items exceeding MAX_RECOVERY_RETRIES
    are permanently failed instead of being re-queued.
    Returns (classified_count, requeued_count).
    """
    MAX_RECOVERY_RETRIES = 10
    classified_count = 0
    requeued_count = 0

    try:
        async with async_session_factory() as db:
            result = await db.execute(_RECOVER_CLASSIFIED_SQL)
            classified_ids = [str(row[0]) for row in result.fetchall()]
            classified_count = len(classified_ids)
    except Exception as exc:
        logger.error("Stale recovery (classified) failed: %s", exc)
        return 0, 0

    if classified_ids:
        try:
            requeued_count = await _requeue_classified(classified_ids)
            # Increment retry_count for recovered items so they don't cycle forever
            async with async_session_factory() as db:
                from uuid import UUID as UuidType

                for item_id in classified_ids:
                    try:
                        await db.execute(
                            update(SourceItem)
                            .where(SourceItem.id == UuidType(item_id))
                            .values(retry_count=SourceItem.retry_count + 1)
                        )
                    except Exception:
                        pass
                await db.commit()
        except Exception as exc:
            logger.error("Stale recovery (classified re-queue) failed: %s", exc)
        else:
            logger.warning(
                "Stale recovery: %d classified items found, %d re-queued",
                classified_count,
                requeued_count,
            )

    # Permanently fail items that exceeded recovery retry limit
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                update(SourceItem)
                .where(SourceItem.status == "classified")
                .where(SourceItem.retry_count >= MAX_RECOVERY_RETRIES)
                .values(
                    status="failed",
                    error_message=f"Wiki integration permanently failed after {MAX_RECOVERY_RETRIES} recovery re-queues — embedding likely hangs or page has issues",
                    started_at=None,
                )
            )
            await db.commit()
            failed_count = result.rowcount or 0
            if failed_count:
                logger.warning(
                    "Stale recovery: permanently failed %d items exceeding %d recovery re-queues",
                    failed_count,
                    MAX_RECOVERY_RETRIES,
                )
    except Exception as exc:
        logger.error("Stale recovery (classified permanent fail) failed: %s", exc)

    return classified_count, requeued_count


async def _sweep_once() -> tuple[int, int, int, int, int, int]:
    """Recover stuck processing/wiki_processing/transcribing/classified items.
    Returns (cpu, wiki, transcribing, pending_transcribe, classified_recovered, classified_requeued)."""
    cpu_count = 0
    wiki_count = 0
    transcribing_count = 0
    pending_transcribe_count = 0
    classified_count = 0
    classified_requeued = 0

    try:
        async with async_session_factory() as db:
            result = await db.execute(_STALE_PROCESSING_SQL)
            await db.commit()
            cpu_count = result.rowcount or 0
    except Exception as exc:
        logger.error("Stale recovery (processing) failed: %s", exc)

    try:
        async with async_session_factory() as db:
            result = await db.execute(_RECOVER_WIKI_SQL)
            revived_ids = [str(row[0]) for row in result.fetchall()]
            await db.commit()
            wiki_count = len(revived_ids)
    except Exception as exc:
        logger.error("Stale recovery (wiki) failed: %s", exc)
        revived_ids = []

    try:
        async with async_session_factory() as db:
            result = await db.execute(_STALE_TRANSCRIBING_SQL)
            await db.commit()
            transcribing_count = result.rowcount or 0
    except Exception as exc:
        logger.error("Stale recovery (transcribing) failed: %s", exc)

    try:
        async with async_session_factory() as db:
            result = await db.execute(_STALE_PENDING_TRANSCRIBE_SQL)
            await db.commit()
            pending_transcribe_count = result.rowcount or 0
    except Exception as exc:
        logger.error("Stale recovery (pending_transcribe) failed: %s", exc)

    if revived_ids:
        try:
            classified_requeued = await _requeue_classified(revived_ids)
        except Exception as exc:
            logger.error("Stale recovery (wiki re-queue) failed: %s", exc)

    try:
        classified_count, classified_requeued_2 = await _recover_classified_items()
        classified_requeued += classified_requeued_2
    except Exception as exc:
        logger.error("Stale recovery (classified) failed: %s", exc)

    if any([cpu_count, wiki_count, transcribing_count, pending_transcribe_count, classified_count, classified_requeued]):
        logger.warning(
            "Stale recovery: %d processing + %d wiki_processing + %d transcribing + %d pending_transcribe + %d classified reset, %d requeued",
            cpu_count,
            wiki_count,
            transcribing_count,
            pending_transcribe_count,
            classified_count,
            classified_requeued,
        )
    return cpu_count, wiki_count, transcribing_count, pending_transcribe_count, classified_count, classified_requeued


async def run_sweeper() -> None:
    global _shutdown
    logger.info(
        "Stale recovery sweeper started (interval=%ds, cpu/transcribing=%dmin, wiki=%dmin, classified=%dmin, worker_timeout=%ds)",
        SWEEP_INTERVAL_SECONDS,
        STALE_THRESHOLD_MINUTES,
        STALE_WIKI_THRESHOLD_MINUTES,
        STALE_CLASSIFIED_THRESHOLD_MINUTES,
        WORKER_ALIVE_SECONDS,
    )

    while not _shutdown:
        try:
            await _sweep_once()
        except Exception as exc:
            error_str = str(exc).lower()
            if "recovery mode" in error_str or "operationalerror" in error_str or "connection" in error_str:
                logger.critical("Stale recovery: DB offline or in recovery. Sleeping for 60s.")
                await asyncio.sleep(60)
                continue
            logger.error("Stale recovery loop error: %s", exc)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)

    logger.info("Stale recovery sweeper stopped")


def stop_sweeper() -> None:
    global _shutdown
    _shutdown = True
