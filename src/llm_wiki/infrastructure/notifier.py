"""Error notification — push to Web UI notification queue + Telegram alert."""

from __future__ import annotations

import json
import logging
import time
from uuid import UUID

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.config import settings
from llm_wiki.infrastructure.persistence.postgres.models import IngestionLog

logger = logging.getLogger(__name__)

# Rate-limit Telegram: max 1 alert per minute
_last_telegram_alert_at: float = 0.0


async def push_error_web(
    source_item_id: UUID,
    error_message: str,
    db: AsyncSession,
    event_type: str = "error",
    metadata: dict | None = None,
) -> None:
    """Log an ingestion event to the ingestion_logs table.

    This table is polled by the Web UI dashboard to display recent errors.
    """
    log_entry = IngestionLog(
        source_item_id=source_item_id,
        event_type=event_type,
        message=error_message[:2000],  # Truncate to reasonable length
        metadata_json=metadata or {},
    )
    db.add(log_entry)
    await db.flush()
    logger.info("Ingestion log: %s — %s", event_type, error_message[:100])


async def send_telegram_alert(message: str) -> bool:
    """Send an alert via Telegram Bot API.

    Rate-limited: max 1 message per 60 seconds to avoid spam.
    Returns True if sent, False if rate-limited (skipped).
    """
    global _last_telegram_alert_at

    if not settings.telegram_bot_token:
        logger.debug("Telegram bot token not configured — skipping alert")
        return False

    now = time.time()
    if now - _last_telegram_alert_at < 60:
        logger.debug("Telegram alert rate-limited — skipping: %s", message[:80])
        return False

    _last_telegram_alert_at = now

    # Get first allowed chat_id
    allowed_ids = settings.allowed_telegram_chat_ids
    if not allowed_ids:
        logger.debug("No allowed Telegram chat IDs configured — skipping alert")
        return False

    chat_ids = [cid.strip() for cid in allowed_ids.split(",") if cid.strip()]
    if not chat_ids:
        return False

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"

    success = False
    for chat_id in chat_ids[:3]:  # Max 3 recipients
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": message[:4000],  # Telegram limit
                        "parse_mode": "HTML",
                    },
                )
            if resp.status_code == 200:
                success = True
                logger.debug("Telegram alert sent to %s", chat_id)
            else:
                logger.warning(
                    "Telegram send to %s failed: HTTP %d — %s",
                    chat_id,
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception as exc:
            logger.error("Telegram alert exception for %s: %s", chat_id, exc)

    return success


async def notify_rate_limit(
    provider: str,
    jobs_paused: int,
    resume_info: str = "",
    db: AsyncSession | None = None,
) -> None:
    """Notify that a rate limit has been hit, pausing N jobs.

    Logs to ingestion_logs if db provided, and sends Telegram alert.
    """
    message = f"Rate limit: {provider}. {jobs_paused} jobs paused." + (
        f" Resume: {resume_info}" if resume_info else ""
    )
    logger.warning(message)

    if db:
        # Log a rate_limit_hit event with a synthetic log entry linked to no specific item
        # Using NULL source_item_id via raw SQL since FK is NOT NULL in the model
        await db.execute(
            text(
                "INSERT INTO ingestion_logs "
                "(id, source_item_id, event_type, message, metadata_json, created_at) "
                "SELECT gen_random_uuid(), id, 'rate_limit_hit', :message, :meta, now() "
                "FROM source_items WHERE status='rate_limited' LIMIT 1"
            ),
            {
                "message": message,
                "meta": json.dumps({"provider": provider, "jobs_paused": jobs_paused}),
            },
        )

    await send_telegram_alert(f"⏸️ {message}")
