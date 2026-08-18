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
import contextlib
import logging
import os
import signal
import time
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.use_cases.ingestion.wiki_integrator import WikiIntegrator
from llm_wiki.config import settings
from llm_wiki.infrastructure.entrypoints.health_server import set_health_state, start_health_server
from llm_wiki.infrastructure.persistence.postgres.database import async_session_factory
from llm_wiki.infrastructure.persistence.postgres.models import (
    IngestionLog,
    PageSection,
    PageSnapshot,
    Source,
    SourceItem,
)
from llm_wiki.infrastructure.persistence.postgres.worker_heartbeat import (
    get_worker_state,
    set_worker_state,
    write_heartbeat,
)
from llm_wiki.infrastructure.persistence.redis.wiki_queue import (
    close_redis,
    pop_wiki_job,
    push_wiki_job,
)
from llm_wiki.infrastructure.telemetry import create_telemetry_adapter
from llm_wiki.infrastructure.telemetry.business_metrics import inc_counter, set_gauge
from llm_wiki.infrastructure.telemetry.metrics_collector import get_metrics
from llm_wiki.presentation.dependencies import traced_embedder, traced_llm
from llm_wiki.shared.datetime_utils import now

CONSUMER_ID = os.getenv("CONSUMER_ID", settings.consumer_id)
logger = logging.getLogger(f"wiki-consumer-{CONSUMER_ID}")

# One telemetry adapter per consumer process — reused across all jobs.
# Each job creates its own root span under this adapter.
_telemetry = create_telemetry_adapter()

_shutdown_requested = False


def _on_terminate(signum: int, frame: object) -> None:
    global _shutdown_requested
    logger.info("Wiki consumer %s received signal %d — shutting down", CONSUMER_ID, signum)
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

    async with sem, httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
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

    for section, vector in zip(page_sections, section_vectors, strict=False):
        section.section_vector = vector
    await db.commit()
    logger.info(
        "Wiki consumer %s: embedded %d section vectors for page %s",
        CONSUMER_ID,
        len(page_sections),
        page_id_str,
    )
    return len(page_sections)


async def _safe_rollback(db: AsyncSession) -> None:
    """Safely rollback a session that may already be closed or expired.

    WikiIntegrator.integrate() calls db.commit() internally. When it raises,
    the session may be in any state (closed, expired, partially committed).
    A naked db.rollback() on an expired session raises MissingGreenlet.
    """
    with contextlib.suppress(Exception):
        await db.rollback()


async def _retry_or_fail(
    item_id: UUID,
    max_retries: int,
    error_message: str,
    event_type: str,
    telemetry_span: object | None = None,
) -> None:
    """Update item status for retry/permanent failure, write log, push to Redis.

    Everything happens in a fresh DB session — no dependency on any caller's
    session (which may be expired/rolled-back/closed after integrate() failure).
    The telemetry end_span is called BEFORE we touch any DB, because after
    integrate() raises, the caller's session is unsafe for ANY operation.
    """
    # End telemetry span first — the caller's session may already be dead.
    if telemetry_span is not None:
        try:
            await _telemetry.end_span(span=telemetry_span, error=error_message[:500])
        except Exception:
            pass

    extra_label = "wiki" if event_type.startswith("wiki") else "embed"
    new_status = "classified"
    new_retry_count = 0
    external_id = ""

    # Phase 1: read + update item in a clean session.
    async with async_session_factory() as db:
        item = await db.get(SourceItem, item_id)
        if item is None:
            return
        external_id = item.external_id or ""
        item.retry_count = (item.retry_count or 0) + 1
        new_retry_count = item.retry_count
        item.error_message = error_message[:2000]
        item.started_at = None
        if item.retry_count > max_retries:
            new_status = "failed"
            item.status = "failed"
            item.error_message = (
                f"Wiki integration permanently failed after {item.retry_count} "
                f"attempts: {error_message[:350]}"
            )
            inc_counter(
                "ingestion_jobs_total",
                {"status": "failed", "stage": extra_label, "worker_id": str(CONSUMER_ID)},
            )
            logger.warning(
                "Wiki consumer %s: %s permanently failed after %d attempts",
                CONSUMER_ID,
                external_id,
                item.retry_count,
            )
        else:
            item.status = "classified"
            inc_counter(
                "ingestion_jobs_total",
                {"status": "retry", "stage": extra_label, "worker_id": str(CONSUMER_ID)},
            )
        log = IngestionLog(
            source_item_id=item_id,
            event_type=event_type,
            message=f"{error_message[:1900]} (attempt {item.retry_count})",
        )
        db.add(log)
        await db.commit()

    # Phase 2: push to Redis (outside DB session so Redis failures don't rollback DB).
    if new_status == "classified":
        try:
            await push_wiki_job(item_id)
        except Exception:
            logger.exception(
                "Wiki consumer %s: failed to push %s to Redis after retry",
                CONSUMER_ID,
                item_id,
            )


async def process_wiki_job(item_id: UUID) -> None:
    """Read item from DB, run wiki integration, embed sections, mark completed."""
    import time as _time

    _job_start = _time.monotonic()
    async with async_session_factory() as db:
        item = await db.get(SourceItem, item_id)
        if item is None:
            logger.warning("Wiki consumer %s: item %s not found in DB", CONSUMER_ID, item_id)
            return

        # Mark as wiki-processing with heartbeat
        item.status = "wiki_processing"
        item.started_at = now()
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
            logger.info(
                "Wiki consumer %s: MERGE_CLASSIFY path — Pass 1 will self-classify", CONSUMER_ID
            )

        source = await db.get(Source, item.source_id)
        source_name = source.name if source else "unknown"

        logger.info(
            "Wiki consumer %s: integrating %s (%s) — %s",
            CONSUMER_ID,
            item.external_id,
            (item.title or "")[:60],
            source_name,
        )

        # Max wiki consumer retries before permanent fail
        WIKI_MAX_RETRIES = 5  # noqa: N806

        # Create a root span for EVERY job — both fresh wiki integration
        # and cached-page retry paths. This ensures end_span is always
        # callable regardless of which branch we take.
        root_span = await _telemetry.start_span(
            name="process_wiki_job",
            kind="chain",
            inputs={
                "video_id": item.external_id or str(item.id),
                "source_name": source_name,
                "transcript_length": len(transcript.get("raw_text", "")),
                "main_topic": classification.get("main_topic", ""),
            },
        )

        # Retry fast-path: if wiki page already exists from a previous attempt,
        # skip the expensive Pass 1→2→3 pipeline and go straight to embedding.
        cached_page_id = data.get("_wiki_page_id")

        if cached_page_id:
            logger.info(
                "Wiki consumer %s: skipping wiki integration for %s — "
                "page %s already created, retrying embedding only",
                CONSUMER_ID,
                item.external_id,
                cached_page_id,
            )
            await _log_event(
                db,
                item.id,
                "wiki_start",
                "Skipping wiki integration (page already created) — retrying embedding",
            )
            await _telemetry.add_metadata(
                root_span, {"fast_path": True, "cached_page_id": cached_page_id}
            )
            wiki_result = {
                "action": "updated",
                "page_id": cached_page_id,
                "page_title": item.title or cached_page_id,
            }
        else:
            await _log_event(db, item.id, "wiki_start", "Integrating into wiki (async consumer)")

            llm = traced_llm("wiki_integrator")
            embedder = traced_embedder("section_embedding")
            # Wire the root span as parent so all nested spanned calls
            # appear under one trace in LangSmith.
            for wrapped in (llm, embedder):
                fn = getattr(wrapped, "set_parent_span", None)
                if callable(fn):
                    fn(root_span)

            integrator = WikiIntegrator(
                llm=llm,
                embedder=embedder,
            )

            # Capture IDs BEFORE integrate() — the session may expire on error.
            _item_pk = item.id
            try:
                wiki_result = await asyncio.wait_for(
                    integrator.integrate(
                        item=item,
                        transcript_text=transcript.get("raw_text", ""),
                        classification=classification,
                        summary_vector=summary_vector,
                        db=db,
                        source_id=item.source_id,
                        source_item_id=_item_pk,
                        published_at=item.published_at,
                        timeout=1800.0,
                    ),
                    timeout=1800.0,
                )
            except TimeoutError:
                logger.error(
                    "Wiki consumer %s: wiki integrate timed out for %s", CONSUMER_ID, _item_pk
                )
                await _safe_rollback(db)
                await _retry_or_fail(
                    _item_pk,
                    WIKI_MAX_RETRIES,
                    "Wiki integration timed out after 30 min",
                    "wiki_timeout",
                    telemetry_span=root_span,
                )
                return
            except Exception as exc:
                error_str = str(exc)
                logger.error(
                    "Wiki consumer %s: wiki integrate failed for %s: %s",
                    CONSUMER_ID, _item_pk, exc,
                )
                await _safe_rollback(db)
                await _retry_or_fail(
                    _item_pk,
                    WIKI_MAX_RETRIES,
                    f"Wiki integration failed: {error_str[:500]}",
                    "wiki_failed",
                    telemetry_span=root_span,
                )
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
        # Child span for section embedding under the root job span
        embed_span = await _telemetry.start_span(
            name="section_embedding",
            kind="embedding",
            inputs={"page_id": page_id_str or ""},
            parent=root_span,
        )
        try:
            section_count = await _save_section_vectors(page_id_str, db)
            await _telemetry.end_span(
                span=embed_span,
                outputs={"section_count": section_count},
            )
        except TimeoutError:
            logger.error(
                "Wiki consumer %s: section embedding timed out for %s", CONSUMER_ID, _item_pk
            )
            await _safe_rollback(db)
            await _retry_or_fail(
                _item_pk,
                WIKI_MAX_RETRIES,
                "Section embedding timed out after 300s",
                "embed_timeout",
                telemetry_span=embed_span,
            )
            return
        except Exception as exc:
            logger.error(
                "Wiki consumer %s: section embedding failed for %s: %s",
                CONSUMER_ID, _item_pk, exc,
            )
            await _safe_rollback(db)
            await _retry_or_fail(
                _item_pk,
                WIKI_MAX_RETRIES,
                f"Section embedding failed: {str(exc)[:500]}",
                "embed_failed",
                telemetry_span=embed_span,
            )
            return
        await _log_event(db, item.id, "section_embed_done", f"{section_count} sections embedded")

        # --- Complete ---
        item.status = "completed"
        item.error_message = None
        await db.commit()
        inc_counter(
            "ingestion_jobs_total",
            {"status": "completed", "stage": "wiki", "worker_id": str(CONSUMER_ID)},
        )
        get_metrics().histogram(
            "ingestion_job_duration_seconds", _time.monotonic() - _job_start, {"stage": "wiki"}
        )

        # Clean up snapshots
        await db.execute(delete(PageSnapshot).where(PageSnapshot.source_item_id == item.id))
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
            "Wiki consumer %s: completed job %s → %s",
            CONSUMER_ID,
            item.external_id,
            wiki_result["action"],
        )

        # End the root span for this job (cached_page_id path or newly created)
        await _telemetry.end_span(
            span=root_span,
            outputs={
                "action": wiki_result.get("action", "unknown"),
                "page_id": wiki_result.get("page_id", ""),
                "page_title": wiki_result.get("page_title", ""),
                "section_count": section_count,
            },
        )


async def _heartbeat_loop(consumer_id: str) -> None:
    """Periodic heartbeat to worker_heartbeats — reads from shared state."""
    while not _shutdown_requested:
        try:
            state = get_worker_state(consumer_id) or {}
            cpu_val = state.get("cpu", 0)
            set_gauge("worker_cpu_percent", float(cpu_val), {"worker_id": str(consumer_id)})
            await write_heartbeat(
                consumer_id,
                worker_type="wiki",
                status=state.get("status", "idle"),
                current_job_id=state.get("job_id"),
                current_stage=state.get("stage"),
                cpu_percent=cpu_val,
                error_message=state.get("error"),
            )
            # Heartbeat age: store wall-clock timestamp of last successful
            # write. Grafana panel computes `time() - worker_heartbeat_age_seconds`
            # to show true age. If write_heartbeat throws the gauge is NOT
            # reset — it climbs until the next successful write.
            set_gauge("worker_heartbeat_age_seconds", time.time(), {"worker_id": str(consumer_id)})
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(15)


async def _recover_orphan_wiki_processing() -> int:
    """Reclaim source_items stuck in 'wiki_processing' after consumer restart/crash.

    When the wiki-consumer crashes or restarts, items that were popped from Redis
    'wiki:queue' but not yet completed are stranded as 'wiki_processing' with no one
    to process them (queue is empty, only consumer writes to queue on retry).

    This recovery scans for items in 'wiki_processing' with started_at older than
    30 minutes, resets them to 'classified', and re-pushes them into the Redis queue
    so the consumer will pick them up again.
    """
    from datetime import timedelta as _td

    _ORPHAN_TIMEOUT = _td(minutes=30)
    _ORPHAN_CUTOFF = now() - _ORPHAN_TIMEOUT

    reclaimed = 0
    async with async_session_factory() as db:
        stmt = select(SourceItem).where(
            SourceItem.status == "wiki_processing",
            SourceItem.started_at < _ORPHAN_CUTOFF,
        )
        result = await db.execute(stmt)
        orphans = result.scalars().all()
        for item in orphans:
            logger.warning(
                "Wiki consumer %s: reclaiming orphan wiki_processing item %s (%s) — stuck since %s",
                CONSUMER_ID,
                item.id,
                item.external_id,
                item.started_at.isoformat() if item.started_at else "unknown",
            )
            item.status = "classified"
            item.started_at = None
            await db.commit()
            await push_wiki_job(item.id)
            reclaimed += 1
    if reclaimed:
        logger.info(
            "Wiki consumer %s: reclaimed %d orphan wiki_processing items", CONSUMER_ID, reclaimed
        )
    return reclaimed


async def main() -> None:
    global _shutdown_requested

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_terminate, sig, None)
        except NotImplementedError:
            signal.signal(sig, _on_terminate)

    logger.info(
        "Wiki consumer %s starting (redis=%s:%d)",
        CONSUMER_ID,
        settings.redis_host,
        settings.redis_port,
    )

    # Recover orphan items before entering main loop — flush any items that were
    # being processed when this consumer (or a previous instance) died.
    try:
        await _recover_orphan_wiki_processing()
    except Exception:
        logger.exception("Wiki consumer %s: orphan recovery failed — continuing", CONSUMER_ID)

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
                if (
                    "recovery mode" in error_str
                    or "operationalerror" in error_str
                    or "connection" in error_str
                ):
                    logger.critical(
                        "Wiki consumer %s: DB offline or in recovery. Sleeping for 60s.",
                        CONSUMER_ID,
                    )
                    await asyncio.sleep(60)
                else:
                    logger.error("Wiki consumer %s error: %s", CONSUMER_ID, exc)
                    await asyncio.sleep(2)

    except asyncio.CancelledError:
        pass
    finally:
        _shutdown_requested = True
        for task in (health_task, heartbeat_task):
            task.cancel()
        for task in (health_task, heartbeat_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await close_redis()

    logger.info("Wiki consumer %s stopped", CONSUMER_ID)


if __name__ == "__main__":
    from llm_wiki.config import settings
    from llm_wiki.infrastructure.telemetry.logging_config import setup_logging

    setup_logging(
        service_name="wiki-consumer",
        log_format=settings.log_format,
        log_level=settings.log_level,
        worker_id=settings.consumer_id,
    )
    asyncio.run(main())
