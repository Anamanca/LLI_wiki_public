"""Wiki consumer entrypoint — standalone process for wiki integration.

Pulls source_item_ids from Redis "wiki:queue", reads the pre-computed
classification + transcript + embeddings from DB, and runs wiki_integrate + section_embed.

Design:
  - 10 instances (docker-compose scale)
  - Each consumer: BLPOP from Redis → DB read → wiki_integrate → section embed → mark completed
  - Graceful shutdown on SIGTERM
  - Heartbeat to worker_heartbeats table (worker_id >= 100)

Usage:
    python -m llm_wiki.infrastructure.entrypoints.wiki_consumer
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.config import settings
from llm_wiki.infrastructure.persistence.postgres.database import async_session_factory
from llm_wiki.infrastructure.persistence.postgres.models import (
    SourceItem,
    Source,
    PageSection,
    PageSnapshot,
    IngestionLog,
)
from llm_wiki.infrastructure.persistence.redis.wiki_queue import pop_wiki_job, push_wiki_job, close_redis
from llm_wiki.infrastructure.persistence.postgres.worker_heartbeat import set_worker_state, get_worker_state, write_heartbeat
from llm_wiki.infrastructure.entrypoints.health_server import start_health_server, set_health_state
from llm_wiki.presentation.dependencies import traced_llm, traced_embedder
from llm_wiki.application.use_cases.ingestion.wiki_integrator import WikiIntegrator
from llm_wiki.infrastructure.embedding.ollama_adapter import OllamaEmbeddingAdapter

CONSUMER_ID = int(os.getenv("CONSUMER_ID", str(settings.consumer_id)))
logger = logging.getLogger(f"wiki-consumer-{CONSUMER_ID}")

_shutdown_requested = False


def _on_terminate(signum: int, frame: object) -> None:
    global _shutdown_requested
    logger.info("Wiki consumer %d received signal %d — shutting down", CONSUMER_ID, signum)
    _shutdown_requested = True


def _rebuild_transcript(data: dict) -> dict:
    """Rebuild transcript dict from stored JSON."""
    segs = data.get("segments", [])
    segments = []
    for s in segs:
        if isinstance(s, dict):
            segments.append(s)
    return {
        "video_id": data.get("video_id", "unknown"),
        "language": data.get("language", "unknown"),
        "duration_seconds": data.get("duration_seconds"),
        "segments": segments,
        "raw_text": data.get("raw_text", ""),
    }


def _rebuild_classification(data: dict) -> dict:
    """Rebuild classification dict from stored JSON."""
    c = data.get("classification", {})
    return {
        "main_topic": c.get("main_topic", "Untitled"),
        "domain": c.get("domain", "general"),
        "subtopics": c.get("subtopics", []),
        "key_entities": c.get("key_entities", []),
        "language": c.get("language", "unknown"),
        "summary_3sentences": c.get("summary_3sentences", ""),
        "existing_pages_to_update": c.get("existing_pages_to_update", []),
    }


async def _log_event(
    db: AsyncSession,
    source_item_id: UUID,
    event_type: str,
    message: str,
    metadata: dict | None = None,
) -> None:
    log = IngestionLog(
        source_item_id=source_item_id,
        event_type=event_type,
        message=message[:2000],
        metadata_json=metadata or {},
    )
    db.add(log)
    await db.flush()


async def _embed_single_text(text: str, sem: asyncio.Semaphore) -> list[float]:
    """Embed a single text via Ollama with bounded concurrency."""
    import httpx

    from llm_wiki.config import settings as _settings

    async with sem:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=10.0)
        ) as client:
            url = f"{_settings.ollama_host}/api/embed"
            resp = await client.post(url, json={"model": "bge-m3", "input": [text], "keep_alive": "0s"})
            resp.raise_for_status()
            data = resp.json()
            vectors = data.get("embeddings", [])
            return vectors[0] if vectors else [0.0] * 1024


async def _save_section_vectors(page_id_str: str | None, db: AsyncSession) -> int:
    """Embed sections in parallel with concurrency-limited individual requests.

    Uses individual Ollama requests (not single batch) so large pages don't
    hit a single 300s timeout. 4 concurrent requests via asyncio.Semaphore.
    Each individual text request completes in ~2-3s on CPU.
    """
    if not page_id_str:
        return 0

    from uuid import UUID as _UUID

    page_uuid = _UUID(page_id_str)
    sections_result = await db.execute(
        select(PageSection)
        .where(PageSection.page_id == page_uuid)
        .order_by(PageSection.section_order)
    )
    page_sections = sections_result.scalars().all()
    if not page_sections:
        return 0

    section_texts = [s.content_markdown or "" for s in page_sections]

    # Parallel individual embedding — each text is its own Ollama request
    # Max 4 concurrent requests to avoid overwhelming the CPU Ollama instance
    sem = asyncio.Semaphore(4)
    tasks = [_embed_single_text(t, sem) for t in section_texts]
    section_vectors = await asyncio.gather(*tasks)

    for section, vector in zip(page_sections, section_vectors):
        section.section_vector = vector
    await db.commit()
    logger.info(
        "Wiki consumer %d: embedded %d section vectors for page %s",
        CONSUMER_ID,
        len(page_sections),
        page_id_str,
    )
    return len(page_sections)


async def process_wiki_job(item_id: UUID) -> None:
    """Read item from DB, run wiki integration, embed sections, mark completed."""
    async with async_session_factory() as db:
        item = await db.get(SourceItem, item_id)
        if item is None:
            logger.warning("Wiki consumer %d: item %s not found in DB", CONSUMER_ID, item_id)
            return

        # Mark as wiki-processing with heartbeat
        item.status = "wiki_processing"
        item.started_at = datetime.now(timezone.utc)
        await db.commit()

        set_worker_state(CONSUMER_ID, "wiki", item.id, "wiki_integrate", 0)

        # Rebuild data
        data = item.transcript_json or {}
        data["video_id"] = item.external_id
        data["raw_text"] = item.transcript_text or ""
        transcript = _rebuild_transcript(data)
        classification = _rebuild_classification(data)
        summary_vector = data.get("summary_vector", [])

        # When MERGE_CLASSIFY_ENABLED, worker sends empty classification —
        # Pass 1 in integrate_wiki will self-classify with 100% coverage.
        use_merged_path = not classification.get("main_topic")
        if use_merged_path:
            logger.info("Wiki consumer %d: MERGE_CLASSIFY path — Pass 1 will self-classify", CONSUMER_ID)

        source = await db.get(Source, item.source_id)
        source_name = source.name if source else "unknown"

        logger.info(
            "Wiki consumer %d: integrating %s (%s) — %s",
            CONSUMER_ID,
            item.external_id,
            (item.title or "")[:60],
            source_name,
        )

        # Max wiki consumer retries before permanent fail
        WIKI_MAX_RETRIES = 5

        # Retry fast-path: if wiki page already exists from a previous attempt,
        # skip the expensive Pass 1→2→3 pipeline and go straight to embedding.
        cached_page_id = data.get("_wiki_page_id")

        if cached_page_id:
            logger.info(
                "Wiki consumer %d: skipping wiki integration for %s — page %s already created, retrying embedding only",
                CONSUMER_ID,
                item.external_id,
                cached_page_id,
            )
            await _log_event(db, item.id, "wiki_start", "Skipping wiki integration (page already created) — retrying embedding")
            wiki_result = {"action": "updated", "page_id": cached_page_id, "page_title": item.title or cached_page_id}
        else:
            await _log_event(db, item.id, "wiki_start", "Integrating into wiki (async consumer)")

            # --- Wiki Integrate ---
            # New clean-arch WikiIntegrator with 3-pass pipeline + LangSmith tracing
            llm = traced_llm("wiki_integrator")
            embedder = traced_embedder("section_embedding")
            integrator = WikiIntegrator(
                llm=llm,
                embedder=embedder,
            )

            try:
                wiki_result = await asyncio.wait_for(
                    integrator.integrate(
                        item=item,
                        transcript_text=transcript.get("raw_text", ""),
                        classification=classification,
                        summary_vector=summary_vector,
                        db=db,
                        source_id=item.source_id,
                        source_item_id=item.id,
                        published_at=item.published_at,
                        timeout=1800.0,
                    ),
                    timeout=1800.0,
                )
            except asyncio.TimeoutError:
                logger.error("Wiki consumer %d: wiki integrate timed out for %s", CONSUMER_ID, item.id)
                await db.rollback()
                item.retry_count = (item.retry_count or 0) + 1
                item.error_message = "Wiki integration timed out after 30 min"
                item.started_at = None
                if item.retry_count > WIKI_MAX_RETRIES:
                    item.status = "failed"
                    item.error_message = f"Wiki integration permanently failed after {item.retry_count} attempts: timeout"
                    logger.warning(
                        "Wiki consumer %d: %s permanently failed after %d wiki attempts",
                        CONSUMER_ID,
                        item.external_id,
                        item.retry_count,
                    )
                else:
                    item.status = "classified"
                await db.commit()
                await _log_event(db, item.id, "wiki_timeout", f"Wiki integration timed out (attempt {item.retry_count})")
                if item.status == "classified":
                    await push_wiki_job(item.id)
                return
            except Exception as exc:
                error_str = str(exc)
                logger.error("Wiki consumer %d: wiki integrate failed for %s: %s", CONSUMER_ID, item.id, exc)
                await db.rollback()
                item.retry_count = (item.retry_count or 0) + 1
                item.error_message = f"Wiki integration failed: {error_str[:500]}"
                item.started_at = None
                if item.retry_count > WIKI_MAX_RETRIES:
                    item.status = "failed"
                    item.error_message = f"Wiki integration permanently failed after {item.retry_count} attempts: {error_str[:400]}"
                    logger.warning(
                        "Wiki consumer %d: %s permanently failed after %d wiki attempts",
                        CONSUMER_ID,
                        item.external_id,
                        item.retry_count,
                    )
                else:
                    item.status = "classified"
                await db.commit()
                await _log_event(db, item.id, "wiki_failed", f"Wiki integration failed (attempt {item.retry_count}): {exc}")
                if item.status == "classified":
                    await push_wiki_job(item.id)
                return

            # Persist page_id so retries skip wiki integration
            data["_wiki_page_id"] = wiki_result.get("page_id")
            item.transcript_json = data
            await db.commit()

            # WikiIntegrator already saved sections in DB, so we only need to embed their vectors.
            # _save_section_vectors handles the rest below.

            await _log_event(
                db,
                item.id,
                "wiki_done",
                f"Wiki {wiki_result['action']}: {wiki_result['page_title']}",
            )

        # --- Embed section vectors ---
        await _log_event(db, item.id, "section_embed_start", "Embedding section vectors")
        set_worker_state(CONSUMER_ID, "wiki", item.id, "section_embed", 0)
        page_id_str = wiki_result.get("page_id")
        try:
            section_count = await _save_section_vectors(page_id_str, db)
        except asyncio.TimeoutError:
            logger.error("Wiki consumer %d: section embedding timed out for %s", CONSUMER_ID, item.id)
            await db.rollback()
            item.retry_count = (item.retry_count or 0) + 1
            item.error_message = "Section embedding timed out after 300s"
            item.started_at = None
            if item.retry_count > WIKI_MAX_RETRIES:
                item.status = "failed"
                item.error_message = f"Wiki integration permanently failed after {item.retry_count} attempts: section embedding timeout"
                logger.warning(
                    "Wiki consumer %d: %s permanently failed after %d embed attempts",
                    CONSUMER_ID,
                    item.external_id,
                    item.retry_count,
                )
            else:
                item.status = "classified"
            await db.commit()
            await _log_event(db, item.id, "embed_timeout", f"Section embedding timed out (attempt {item.retry_count})")
            if item.status == "classified":
                await push_wiki_job(item.id)
            return
        except Exception as exc:
            logger.error("Wiki consumer %d: section embedding failed for %s: %s", CONSUMER_ID, item.id, exc)
            await db.rollback()
            item.retry_count = (item.retry_count or 0) + 1
            item.error_message = f"Section embedding failed: {str(exc)[:500]}"
            item.started_at = None
            if item.retry_count > WIKI_MAX_RETRIES:
                item.status = "failed"
                item.error_message = f"Wiki integration permanently failed after {item.retry_count} attempts: section embedding error"
                logger.warning(
                    "Wiki consumer %d: %s permanently failed after %d embed attempts",
                    CONSUMER_ID,
                    item.external_id,
                    item.retry_count,
                )
            else:
                item.status = "classified"
            await db.commit()
            await _log_event(db, item.id, "embed_failed", f"Section embedding failed (attempt {item.retry_count}): {exc}")
            if item.status == "classified":
                await push_wiki_job(item.id)
            return
        await _log_event(db, item.id, "section_embed_done", f"{section_count} sections embedded")

        # --- Complete ---
        item.status = "completed"
        item.error_message = None
        await db.commit()

        # Clean up snapshots
        await db.execute(
            delete(PageSnapshot).where(PageSnapshot.source_item_id == item.id)
        )
        await db.commit()

        await _log_event(
            db,
            item.id,
            "completed",
            f"Successfully processed: {wiki_result['action']} page '{wiki_result['page_title']}'",
        )
        await db.commit()

        set_worker_state(CONSUMER_ID, "idle")
        logger.info(
            "Wiki consumer %d: completed job %s → %s",
            CONSUMER_ID,
            item.external_id,
            wiki_result["action"],
        )


async def _heartbeat_loop(consumer_id: int) -> None:
    """Periodic heartbeat to worker_heartbeats — reads from shared state."""
    while not _shutdown_requested:
        try:
            state = get_worker_state(consumer_id) or {}
            await write_heartbeat(
                consumer_id,
                status=state.get("status", "idle"),
                current_job_id=state.get("job_id"),
                current_stage=state.get("stage"),
                cpu_percent=state.get("cpu", 0),
                error_message=state.get("error"),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(15)


async def main() -> None:
    global _shutdown_requested

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_terminate, sig, None)
        except NotImplementedError:
            signal.signal(sig, _on_terminate)

    logger.info(
        "Wiki consumer %d starting (redis=%s:%d)",
        CONSUMER_ID,
        settings.redis_host,
        settings.redis_port,
    )

    health_task = asyncio.create_task(start_health_server(), name="health-server")
    set_health_state("running")

    heartbeat_task = asyncio.create_task(_heartbeat_loop(CONSUMER_ID), name="wiki-heartbeat")

    try:
        while not _shutdown_requested:
            try:
                item_id = await pop_wiki_job(timeout=5.0)
                if item_id is None:
                    continue

                await process_wiki_job(item_id)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                set_worker_state(CONSUMER_ID, "idle")
                error_str = str(exc).lower()
                if "recovery mode" in error_str or "operationalerror" in error_str or "connection" in error_str:
                    logger.critical("Wiki consumer %d: DB offline or in recovery. Sleeping for 60s.", CONSUMER_ID)
                    await asyncio.sleep(60)
                else:
                    logger.error("Wiki consumer %d error: %s", CONSUMER_ID, exc)
                    await asyncio.sleep(2)

    except asyncio.CancelledError:
        pass
    finally:
        _shutdown_requested = True
        for task in (health_task, heartbeat_task):
            task.cancel()
        for task in (health_task, heartbeat_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await close_redis()

    logger.info("Wiki consumer %d stopped", CONSUMER_ID)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    asyncio.run(main())
