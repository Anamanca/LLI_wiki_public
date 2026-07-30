"""Job processor — continuous worker loop with PG SKIP LOCKED queue.

Responsibilities:
  - claim_job(): SELECT ... FOR UPDATE SKIP LOCKED
  - process_job(): extract → classify → embed → wiki_integrate
  - handle_job_failure(): retry once, rollback snapshots on failure
  - Graceful shutdown on SIGTERM

Usage:
    python -m llm_wiki.infrastructure.entrypoints.cpu_worker
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import signal
import time
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.config import settings
from llm_wiki.infrastructure.cpu_guard import cpu_safe_to_proceed, get_current_cpu
from llm_wiki.infrastructure.entrypoints.health_server import set_health_state, start_health_server
from llm_wiki.infrastructure.notifier import push_error_web, send_telegram_alert
from llm_wiki.infrastructure.persistence.postgres.database import async_session_factory
from llm_wiki.infrastructure.persistence.postgres.models import (
    IngestionLog,
    MediaAsset,
    Page,
    PageLink,
    PageSection,
    PageSnapshot,
    Source,
    SourceItem,
)
from llm_wiki.infrastructure.persistence.postgres.worker_heartbeat import set_worker_state
from llm_wiki.infrastructure.persistence.redis.wiki_queue import push_wiki_job
from llm_wiki.infrastructure.telemetry import create_telemetry_adapter
from llm_wiki.infrastructure.telemetry.business_metrics import inc_counter, set_gauge
from llm_wiki.infrastructure.telemetry.metrics_collector import get_metrics
from llm_wiki.shared.datetime_utils import now

logger = logging.getLogger(__name__)

# One telemetry adapter per worker process — reused across all jobs.
_telemetry = create_telemetry_adapter()

# Global shutdown flag
_shutdown_requested = False

WORKER_ID = int(os.getenv("WORKER_ID", str(settings.worker_id)))


def _on_sigterm(signum: int, frame: Any) -> None:
    global _shutdown_requested
    logger.info("Worker %d received signal %d — graceful shutdown initiated", WORKER_ID, signum)
    _shutdown_requested = True


async def claim_job(db: AsyncSession) -> SourceItem | None:
    """Claim the next pending job using SELECT ... FOR UPDATE SKIP LOCKED.

    Returns the SourceItem or None if no jobs are available.
    """
    stmt = (
        select(SourceItem)
        .where(SourceItem.status == "pending")
        .where(
            # Skip items that are rate-limited (retry_after in the future)
            (SourceItem.retry_after.is_(None)) | (SourceItem.retry_after <= now())
        )
        .order_by(SourceItem.priority.asc(), SourceItem.published_at.desc().nullslast())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    item = result.scalar()
    if item is None:
        return None

    # Mark as processing with heartbeat timestamp
    item.status = "processing"
    item.started_at = now()
    item.error_message = None  # Clear stale error from previous attempt
    await db.commit()
    await db.refresh(item)
    logger.debug("Worker %d: Claimed job %s (title=%s)", WORKER_ID, item.id, item.title)
    return item


async def _build_job_context(item: SourceItem, db: AsyncSession) -> dict[str, Any]:
    """Fetch source info for the job."""
    source = await db.get(Source, item.source_id)
    return {
        "source_item_id": str(item.id),
        "source_id": str(item.source_id),
        "source_name": source.name if source else "unknown",
        "video_id": item.external_id,
        "video_url": item.url or f"https://www.youtube.com/watch?v={item.external_id}",
        "video_title": item.title or "",
        "published_at": item.published_at.isoformat() if item.published_at else None,
    }


async def _log_event(
    db: AsyncSession,
    source_item_id: UUID,
    event_type: str,
    message: str,
    metadata: dict | None = None,
) -> None:
    """Insert an ingestion log entry."""
    log = IngestionLog(
        source_item_id=source_item_id,
        event_type=event_type,
        message=message[:2000],
        metadata_json=metadata or {},
    )
    db.add(log)
    await db.flush()


async def _rollback_snapshots(source_item_id: UUID, db: AsyncSession) -> None:
    """Rollback all wiki changes made by this job using saved snapshots.

    Steps:
      1. Query all snapshots for this source_item_id
      2. For each snapshot:
         - If content_markdown IS NULL: page was CREATED by this job → DELETE page
         - If content_markdown IS NOT NULL: page was UPDATED → restore old content
      3. Unlink media_assets first (SET section_id = NULL)
      4. DELETE old sections, INSERT from snapshot
      5. DELETE snapshots for this job
    """
    logger.info("Rolling back snapshots for source_item_id=%s", source_item_id)

    # Fetch all snapshots for this job
    snapshots_result = await db.execute(
        select(PageSnapshot).where(PageSnapshot.source_item_id == source_item_id)
    )
    snapshots = snapshots_result.scalars().all()

    if not snapshots:
        logger.info("No snapshots to rollback for %s", source_item_id)
        return

    for snapshot in snapshots:
        page_id = snapshot.page_id

        if snapshot.content_markdown is None:
            # Page was created by this job — delete it entirely
            logger.info("Rollback: deleting page %s (created by failed job)", page_id)

            # Unlink media_assets
            await db.execute(
                update(MediaAsset)
                .where(MediaAsset.page_id == page_id)
                .values(page_id=None, section_id=None)
            )
            await db.flush()

            # Delete sections, links, then the page
            await db.execute(delete(PageSection).where(PageSection.page_id == page_id))
            await db.execute(delete(PageLink).where(PageLink.from_page_id == page_id))
            await db.execute(delete(PageLink).where(PageLink.to_page_id == page_id))
            await db.execute(delete(Page).where(Page.id == page_id))
        else:
            # Page was updated — restore old content
            logger.info("Rollback: restoring page %s content", page_id)

            # Restore page content
            await db.execute(
                update(Page)
                .where(Page.id == page_id)
                .values(content_markdown=snapshot.content_markdown)
            )
            await db.flush()

            # Unlink media_assets from old sections
            await db.execute(
                update(MediaAsset)
                .where(
                    MediaAsset.section_id.in_(
                        select(PageSection.id).where(PageSection.page_id == page_id)
                    )
                )
                .values(section_id=None)
            )
            await db.flush()

            # Delete existing sections
            await db.execute(delete(PageSection).where(PageSection.page_id == page_id))
            await db.flush()

            # Re-insert sections from snapshot
            sections_data = snapshot.sections_jsonb or []
            for sec in sections_data:
                section = PageSection(
                    page_id=page_id,
                    title=sec.get("title", ""),
                    content_markdown=sec.get("content_markdown", ""),
                    section_order=sec.get("section_order", 0),
                    source_ref=sec.get("source_ref", ""),
                )
                db.add(section)

    # Delete all snapshots for this job
    await db.execute(delete(PageSnapshot).where(PageSnapshot.source_item_id == source_item_id))
    await db.flush()
    logger.info("Rollback complete for source_item_id=%s", source_item_id)


STAGE_TIMEOUTS: dict[str, float] = {
    "extracting": 14400.0,  # 4h — long videos (2-3h) need generous timeout for faster-whisper
    "classifying": 1800.0,  # 30 min
    "embedding": 1800.0,  # 30 min
    "wiki": 1800.0,  # 30 min
    "default": 3600.0,  # 1h fallback
}


async def _embed_texts_ollama(texts: list[str]) -> list[list[float]]:
    """Embed texts via Ollama bge-m3 model."""
    import httpx

    if not texts:
        return []

    clean_texts = [t.strip() for t in texts if t and t.strip()]
    if not clean_texts:
        return [[0.0] * 1024 for _ in texts]

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        url = f"{settings.ollama_host}/api/embed"
        resp = await client.post(
            url,
            json={"model": "bge-m3", "input": clean_texts, "keep_alive": "0s"},
        )
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings", [])

    # Map back to original indices
    result: list[list[float]] = []
    emb_iter = iter(embeddings)
    for t in texts:
        if t and t.strip():
            try:
                result.append(next(emb_iter))
            except StopIteration:
                result.append([0.0] * 1024)
        else:
            result.append([0.0] * 1024)
    return result


async def process_job(item: SourceItem, db: AsyncSession) -> None:
    """Execute the full ingestion pipeline for one SourceItem.

    Stages: extract → classify → embed → wiki_integrate
    Each stage has a timeout — exceeding it raises TimeoutError and triggers retry.
    """
    import time as _time

    _job_start = _time.monotonic()
    ctx = await _build_job_context(item, db)
    logger.info(
        "Worker %d: Processing job %s: %s (%s)",
        WORKER_ID,
        ctx["video_id"],
        ctx["video_title"][:80],
        ctx["source_name"],
    )

    # Root telemetry span for this CPU pipeline job.
    root_span = await _telemetry.start_span(
        name="process_cpu_job",
        kind="chain",
        inputs={
            "video_id": ctx["video_id"],
            "video_title": (ctx["video_title"] or "")[:120],
            "source_name": ctx["source_name"],
            "has_cached_transcript": bool(item.transcript_text),
        },
    )

    def _heartbeat(stage: str, error: str | None = None) -> None:
        set_worker_state(WORKER_ID, "processing", item.id, stage, int(get_current_cpu()), error)

    # --- Stage 0+1: Extract transcript (includes accessibility check) ---
    await _log_event(db, item.id, "extract_start", "Extracting transcript")
    _heartbeat("extracting")
    transcript_dict = None

    cached = item.transcript_text
    if cached:
        logger.info(
            "Worker %d: Job %s: using cached transcript from GPU worker", WORKER_ID, ctx["video_id"]
        )
        cached_json = item.transcript_json or {}
        segs = cached_json.get("segments", [])
        transcript_dict = {
            "video_id": ctx["video_id"],
            "language": cached_json.get("language", "unknown"),
            "duration_seconds": cached_json.get("duration_seconds"),
            "segments": segs,
            "raw_text": cached[:100_000],
        }
    else:
        from llm_wiki.application.use_cases.ingestion.extractor import (
            classify_extract_error,
            extract_transcript,
        )

        try:
            transcript = await asyncio.wait_for(
                extract_transcript(ctx["video_url"], ctx["video_id"]),
                timeout=STAGE_TIMEOUTS["extracting"],
            )
            transcript_dict = {
                "video_id": transcript.video_id,
                "language": transcript.language,
                "duration_seconds": transcript.duration_seconds,
                "segments": [
                    {"start": s.start, "end": s.end, "text": s.text} for s in transcript.segments
                ],
                "raw_text": transcript.raw_text,
            }
        except Exception as exc:
            # Classify error: permanent (members/private/deleted) or transient (network/429)
            err_msg = str(exc)
            new_status, is_permanent = classify_extract_error(err_msg)
            if is_permanent:
                await _telemetry.end_span(span=root_span, error=f"extract: {err_msg[:400]}")
                if new_status == "scheduled":
                    item.status = "pending"
                    item.retry_after = now() + timedelta(hours=12)
                    item.error_message = f"Video not yet available: {new_status}"
                    await _log_event(db, item.id, "video_scheduled", item.error_message)
                    await db.commit()
                    logger.info(
                        "Worker %d: Job %s: scheduled (premieres later) — retry after 12h",
                        WORKER_ID,
                        ctx["video_id"],
                    )
                else:
                    item.status = new_status
                    item.started_at = None
                    item.error_message = f"Video not accessible: {new_status}"
                    await _log_event(db, item.id, "video_inaccessible", item.error_message)
                    await db.commit()
                    inc_counter(
                        "ingestion_jobs_total",
                        {"status": new_status, "stage": "extract", "worker_id": str(WORKER_ID)},
                    )
                    logger.info(
                        "Worker %d: Job %s: permanently %s — skipping",
                        WORKER_ID,
                        ctx["video_id"],
                        new_status,
                    )
                return
            else:
                # Transient error — queue for retry with backoff
                await _telemetry.end_span(
                    span=root_span, error=f"extract transient: {err_msg[:400]}"
                )
                logger.warning(
                    "Worker %d: Job %s: extract transient error: %s",
                    WORKER_ID,
                    ctx["video_id"],
                    exc,
                )
                await handle_job_failure(item, exc, db)
                return

    if transcript_dict is None or not transcript_dict.get("segments"):
        reason = "no_captions_t3_fail" if transcript_dict is None else "no_captions"
        log_msg = (
            "All 4 tiers exhausted — no captions available"
            if transcript_dict is None
            else "No caption segments found"
        )
        await _telemetry.end_span(span=root_span, error=log_msg)
        item.status = reason
        item.started_at = None
        item.error_message = log_msg
        await _log_event(db, item.id, reason, log_msg)
        await db.commit()
        inc_counter(
            "ingestion_jobs_total",
            {"status": reason, "stage": "extract", "worker_id": str(WORKER_ID)},
        )
        logger.info("Worker %d: Job %s: %s — skipping", WORKER_ID, ctx["video_id"], log_msg)
        return

    await _log_event(
        db,
        item.id,
        "extract_done",
        f"Extracted {len(transcript_dict.get('segments', []))} segments",
    )

    # --- Stage 2: Classify ---
    merge_classify_enabled = os.getenv("MERGE_CLASSIFY_ENABLED", "false").lower() == "true"

    if merge_classify_enabled:
        # Path A: Skip classifier — wiki_consumer will use Pass 1 classification
        logger.info(
            "Worker %d: MERGE_CLASSIFY_ENABLED=true — skipping classifier, "
            "wiki_consumer will self-classify",
            WORKER_ID,
        )
        classification_data = {
            "main_topic": "",
            "domain": "",
            "subtopics": [],
            "key_entities": [],
            "language": "vi",
            "summary_3sentences": "",
            "existing_pages_to_update": [],
        }
        summary_vector_from_embed: list[float] = []
    else:
        # Path B: Legacy — run classifier first (cold fallback)
        await _log_event(db, item.id, "classify_start", "Classifying transcript")
        _heartbeat("classifying")
        from llm_wiki.application.use_cases.ingestion.classifier import classify_transcript
        from llm_wiki.infrastructure.llm.openai_adapter import OpenAIAdapter
        from llm_wiki.infrastructure.llm.traced_llm_wrapper import TracedLLMWrapper

        raw_llm = OpenAIAdapter()
        # Wrap classifier LLM with tracing so each API call appears
        # as a child span under process_cpu_job.
        llm_client = TracedLLMWrapper(
            raw_llm,
            _telemetry,
            model=getattr(raw_llm, "model", "unknown"),
            parent_span=root_span,
        )
        try:
            classification_data = await asyncio.wait_for(
                classify_transcript(transcript_dict, llm_client),
                timeout=STAGE_TIMEOUTS["classifying"],
            )
        except Exception as exc:
            await _telemetry.end_span(span=root_span, error=f"classify failed: {str(exc)[:400]}")
            logger.error("Worker %d: Classification failed for %s: %s", WORKER_ID, item.id, exc)
            await handle_job_failure(item, exc, db)
            return

        classify_msg = (
            f"Classified: {classification_data.get('main_topic', '')}, "
            f"lang={classification_data.get('language', '')}"
        )
        await _log_event(db, item.id, "classify_done", classify_msg)

        # Build summary_vector from classifier output
        classification_text = classification_data.get(
            "summary_3sentences", ""
        ) or classification_data.get("main_topic", "")
        if not classification_text.strip():
            await _log_event(
                db, item.id, "embed_skip", "Classification returned no text — skipping embedding"
            )
            summary_vector_from_embed = []
        else:
            try:
                embed_result = await asyncio.wait_for(
                    _embed_texts_ollama([classification_text]),
                    timeout=STAGE_TIMEOUTS["embedding"],
                )
                summary_vector_from_embed = embed_result[0] if embed_result else []
            except Exception as exc:
                logger.error("Worker %d: Embedding failed for %s: %s", WORKER_ID, item.id, exc)
                summary_vector_from_embed = []

    # --- Stage 3: Save & delegate to wiki consumer ---
    # Store everything the wiki consumer needs in JSON
    summary_vector = summary_vector_from_embed
    item.transcript_text = transcript_dict.get("raw_text", "")[:500_000]  # Cap at 500KB
    item.transcript_json = {
        "segments": transcript_dict.get("segments", []),
        "language": classification_data.get("language", transcript_dict.get("language", "unknown")),
        "duration_seconds": transcript_dict.get("duration_seconds"),
        "summary_vector": summary_vector,
        "classification": {
            "main_topic": classification_data.get("main_topic", ""),
            "subtopics": classification_data.get("subtopics", []),
            "summary_3sentences": classification_data.get("summary_3sentences", ""),
            "domain": classification_data.get("domain", "general"),
            "key_entities": classification_data.get("key_entities", []),
            "existing_pages_to_update": classification_data.get("existing_pages_to_update", []),
        },
    }
    item.status = "classified"
    item.started_at = None
    await db.commit()

    queue_len = await push_wiki_job(item.id)
    set_gauge("ingestion_queue_depth", float(queue_len), {"queue": "wiki"})
    inc_counter(
        "ingestion_jobs_total",
        {"status": "classified", "stage": "cpu_done", "worker_id": str(WORKER_ID)},
    )
    get_metrics().histogram(
        "ingestion_job_duration_seconds", _time.monotonic() - _job_start, {"stage": "cpu"}
    )
    sections_count = len(classification_data.get("subtopics", []) or [])
    await _log_event(
        db, item.id, "wiki_queued", f"Queued for wiki integration (topics: {sections_count})"
    )
    await db.commit()

    # End the root telemetry span — job is done from cpu_worker's perspective.
    await _telemetry.end_span(
        span=root_span,
        outputs={
            "status": "classified",
            "main_topic": classification_data.get("main_topic", ""),
            "language": classification_data.get("language", ""),
            "transcript_segments": len(transcript_dict.get("segments", [])),
            "merge_classify_enabled": merge_classify_enabled,
        },
    )

    _heartbeat("idle")
    logger.info("Worker %d: Job %s classified and queued for wiki", WORKER_ID, ctx["video_id"])


async def handle_job_failure(
    item: SourceItem,
    error: Exception,
    db: AsyncSession,
) -> None:
    """Handle a failed job: classify error and decide retry vs permanent fail.

    Rate-limit errors (HTTP 429): do NOT count toward retry_count.
    Other errors: retry 2 times, then permanent fail.
    """
    error_msg = str(error)[:500]
    error_str = error_msg.lower()
    is_rate_limit = "429" in error_str or "rate" in error_str or "too many" in error_str
    is_payment_required = "402" in error_str or "payment" in error_str or "quota" in error_str
    is_temporary_pause = is_rate_limit or is_payment_required

    if is_temporary_pause:
        logger.warning(
            "Worker %d: Job %s paused (402/429/quota): %s", WORKER_ID, item.id, error_msg[:120]
        )
    else:
        logger.error(
            "Worker %d: Job %s failed (retry_count=%d): %s",
            WORKER_ID,
            item.id,
            item.retry_count,
            error_msg,
        )

    set_worker_state(WORKER_ID, "error", item.id, None, int(get_current_cpu()), error_msg)

    # Rollback wiki changes
    try:
        await _rollback_snapshots(item.id, db)
    except Exception as rollback_exc:
        logger.error("Worker %d: Rollback failed for job %s: %s", WORKER_ID, item.id, rollback_exc)

    item.started_at = None
    item.error_message = error_msg

    if is_temporary_pause:
        # Rate-limit/402: reset to pending with 24-hour backoff
        # (pending until manual reset or next day). DON'T increment retry_count
        item.status = "pending"
        item.retry_after = now() + timedelta(hours=24)
        await _log_event(
            db,
            item.id,
            "retry",
            f"Quota/Payment/Rate-limited, queued for retry in 24h: {error_msg[:200]}",
        )
        logger.warning(
            "Worker %d: Job %s rate-limited/402, retry after %s",
            WORKER_ID,
            item.id,
            item.retry_after,
        )
        await push_error_web(item.id, error_msg, db, event_type="api_limit")
        # Ensure telegram alert for 402/Quota limits specifically to notify admin
        if is_payment_required:
            source = await db.get(Source, item.source_id)
            source_name = source.name if source else "unknown"
            alert = (
                f"🚨 API Quota/Payment limit reached: [{source_name}] "
                f"{item.title or item.external_id}\n{error_msg[:200]}"
            )
            await send_telegram_alert(alert)
    else:
        item.retry_count = (item.retry_count or 0) + 1
        if item.retry_count <= 2:
            item.status = "pending"
            item.retry_after = now() + timedelta(seconds=60)
            await _log_event(
                db,
                item.id,
                "retry",
                f"Failed attempt {item.retry_count}, queued for retry: {error_msg[:200]}",
            )
            logger.warning(
                "Worker %d: Job %s queued for retry (attempt %d)",
                WORKER_ID,
                item.id,
                item.retry_count,
            )
        else:
            item.status = "failed"
            await _log_event(
                db,
                item.id,
                "error",
                f"Permanently failed after {item.retry_count} attempts: {error_msg[:200]}",
            )
            source = await db.get(Source, item.source_id)
            source_name = source.name if source else "unknown"
            await push_error_web(item.id, error_msg, db, event_type="error")
            alert = (
                f"⚠️ Ingest failed: [{source_name}] "
                f"{item.title or item.external_id}\n{error_msg[:200]}"
            )
            await send_telegram_alert(alert)
            logger.error(
                "Worker %d: Job %s permanently failed after %d attempts",
                WORKER_ID,
                item.id,
                item.retry_count,
            )

    await db.commit()


async def worker_loop() -> None:
    """Continuous worker loop that claims and processes jobs.

    Uses PG SKIP LOCKED for cooperative job claiming across multiple workers.
    Graceful shutdown on SIGTERM/SIGINT.

    Adds random startup delay (0-5 min) to prevent cold-start rate-limit spikes
    when all workers hit YouTube API simultaneously.
    """
    startup_delay = random.randint(0, 300)
    logger.info("Worker %d starting (startup delay: %ds)", WORKER_ID, startup_delay)
    await asyncio.sleep(startup_delay)

    while not _shutdown_requested:
        try:
            # CPU admission control — pause if system CPU is too high
            if not await cpu_safe_to_proceed():
                set_worker_state(WORKER_ID, "idle", cpu=int(get_current_cpu()))
                await asyncio.sleep(30)
                continue

            async with async_session_factory() as db:
                job = await claim_job(db)
                if job is None:
                    set_worker_state(WORKER_ID, "idle", cpu=int(get_current_cpu()))
                    # Report pending queue depth every idle cycle
                    from sqlalchemy import func, select

                    from llm_wiki.infrastructure.persistence.postgres.models import SourceItem

                    pending_count = (
                        await db.execute(
                            select(func.count())
                            .select_from(SourceItem)
                            .where(SourceItem.status == "pending")
                        )
                    ).scalar() or 0
                    set_gauge("ingestion_queue_depth", float(pending_count), {"queue": "cpu"})
                    await asyncio.sleep(10)
                    continue

                try:
                    await process_job(job, db)
                except Exception as exc:
                    set_worker_state(WORKER_ID, "error", job.id, error=str(exc)[:500])
                    try:
                        async with async_session_factory() as rollback_db:
                            fresh_item = await rollback_db.get(SourceItem, job.id)
                            if fresh_item:
                                await handle_job_failure(fresh_item, exc, rollback_db)
                    except Exception as handler_exc:
                        logger.critical(
                            "Worker %d failed to handle job failure for %s: %s",
                            WORKER_ID,
                            job.id,
                            handler_exc,
                        )

        except Exception as loop_exc:
            error_str = str(loop_exc).lower()
            if (
                "recovery mode" in error_str
                or "operationalerror" in error_str
                or "connection" in error_str
            ):
                logger.critical(
                    "Worker %d: DB offline or in recovery. Sleeping for 60s.", WORKER_ID
                )
                with contextlib.suppress(Exception):
                    set_worker_state(
                        WORKER_ID, "error", error="DB Offline / Recovery Mode. Paused 60s."
                    )
                await asyncio.sleep(60)
            else:
                logger.error("Worker %d loop error: %s", WORKER_ID, loop_exc)
                with contextlib.suppress(Exception):
                    set_worker_state(WORKER_ID, "error", error=str(loop_exc)[:500])
                await asyncio.sleep(5)

    logger.info("Worker %d shutting down", WORKER_ID)
    set_worker_state(WORKER_ID, "idle")


async def _heartbeat_loop() -> None:
    """Periodic heartbeat to worker_heartbeats — reads from shared state."""
    from llm_wiki.infrastructure.persistence.postgres.worker_heartbeat import get_worker_state
    from llm_wiki.infrastructure.persistence.postgres.worker_heartbeat import (
        write_heartbeat as write_hb,
    )

    while not _shutdown_requested:
        try:
            state = get_worker_state(WORKER_ID) or {}
            cpu_val = state.get("cpu", 0)
            # Emit worker Prometheus gauges
            set_gauge("worker_cpu_percent", float(cpu_val), {"worker_id": str(WORKER_ID)})
            await write_hb(
                WORKER_ID,
                status=state.get("status", "idle"),
                current_job_id=state.get("job_id"),
                current_stage=state.get("stage"),
                cpu_percent=cpu_val,
                error_message=state.get("error"),
            )
            # Heartbeat age: store wall-clock timestamp of last successful
            # write. Grafana panel computes `time() - worker_heartbeat_age_seconds`
            # to show true age. If write_hb throws the gauge is NOT reset —
            # it climbs until the next successful write, surfacing the stall.
            set_gauge("worker_heartbeat_age_seconds", time.time(), {"worker_id": str(WORKER_ID)})
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
            loop.add_signal_handler(sig, _on_sigterm, sig, None)
        except NotImplementedError:
            signal.signal(sig, _on_sigterm)

    logger.info("CPU worker %d starting", WORKER_ID)

    health_task = asyncio.create_task(start_health_server(), name="health-server")
    set_health_state("running")

    heartbeat_task = asyncio.create_task(_heartbeat_loop(), name="worker-heartbeat")

    try:
        await worker_loop()
    except asyncio.CancelledError:
        pass
    finally:
        _shutdown_requested = True
        for task in (health_task, heartbeat_task):
            task.cancel()
        for task in (health_task, heartbeat_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    logger.info("CPU worker %d stopped", WORKER_ID)


if __name__ == "__main__":
    from llm_wiki.config import settings
    from llm_wiki.infrastructure.telemetry.logging_config import setup_logging

    setup_logging(
        service_name="cpu-worker",
        log_format=settings.log_format,
        log_level=settings.log_level,
        worker_id=settings.worker_id,
    )
    asyncio.run(main())
