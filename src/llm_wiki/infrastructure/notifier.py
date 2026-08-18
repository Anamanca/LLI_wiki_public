"""Error notification — push to Web UI notification queue + Telegram alert."""

from __future__ import annotations

import logging
import time
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.config import settings
from llm_wiki.infrastructure.persistence.postgres.models import IngestionLog

logger = logging.getLogger(__name__)

# Rate-limit Telegram: max 1 alert per minute
_last_telegram_alert_at: float = 0.0


async def push_error_web(
    source_item_id: UUID | None,
    error_message: str,
    db: AsyncSession,
    event_type: str = "error",
    metadata: dict | None = None,
) -> None:
    """Log an ingestion event to the ingestion_logs table.

    This table is polled by the Web UI dashboard to display recent errors.
    ``source_item_id`` may be None for system-level alerts (e.g. API-key
    failures) that are not tied to a specific item.
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


async def push_system_alert(
    message: str,
    event_type: str = "api_key_error",
    metadata: dict | None = None,
) -> None:
    """Persist a system-level alert (no associated source_item) to ingestion_logs.

    Used for API-key failures detected outside any single ingestion job. Uses
    its own session so it can be called from the LLM adapter layer without an
    existing DB session in scope.
    """
    from llm_wiki.infrastructure.persistence.postgres.database import async_session_factory

    try:
        async with async_session_factory() as session:
            log_entry = IngestionLog(
                source_item_id=None,
                event_type=event_type,
                message=message[:2000],
                metadata_json=metadata or {},
            )
            session.add(log_entry)
            await session.commit()
        logger.info("System alert: %s — %s", event_type, message[:100])
    except Exception:
        logger.exception("Failed to persist system alert (%s)", event_type)


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


