"""YouTube Data API v3 client — poll channel for new videos, backfill playlists/videos."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.config import settings
from llm_wiki.infrastructure.persistence.postgres.models import Source, SourceItem

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
QUOTA_BUDGET = 10_000  # units per day — track remaining via _quota_used
_quota_used: dict[str, int] = {}  # source_id -> units used today


def _consume_quota(source_id: str, units: int) -> None:
    _quota_used[source_id] = _quota_used.get(source_id, 0) + units


class YouTubeQuotaExceeded(Exception):
    """Raised when YouTube returns 403 quotaExceeded."""
    pass


def _is_quota_exceeded_error(status_code: int, body: dict) -> bool:
    """Detect YouTube quota exhaustion from the response."""
    if status_code == 403:
        errors = body.get("error", {}).get("errors", [])
        for err in errors:
            if err.get("reason") == "quotaExceeded":
                return True
    return False


async def _youtube_get(
    path: str,
    params: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Make a YouTube Data API v3 GET request with quota error detection."""
    params = dict(params)
    params["key"] = settings.youtube_api_key

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        base_url=YOUTUBE_API_BASE,
    ) as client:
        resp = await client.get(path, params=params)
    body = resp.json()
    if _is_quota_exceeded_error(resp.status_code, body):
        raise YouTubeQuotaExceeded("YouTube daily quota exhausted")
    if resp.status_code != 200:
        raise RuntimeError(
            f"YouTube API HTTP {resp.status_code}: {body.get('error', {}).get('message', body)}"
        )
    return body


async def _paginate(
    path: str,
    params: dict[str, Any],
    item_key: str = "items",
    max_pages: int = 20,
    quota_per_page: int = 1,
) -> list[dict[str, Any]]:
    """Generic pageToken-based pagination for YouTube API.

    Returns accumulated items across all pages.
    """
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    for page_num in range(max_pages):
        p = dict(params)
        p["maxResults"] = 50
        if page_token:
            p["pageToken"] = page_token
        body = await _youtube_get(path, p)
        items.extend(body.get(item_key, []))
        page_token = body.get("nextPageToken")
        logger.debug(
            "YouTube paginate page %d: fetched %d items, nextToken=%s",
            page_num + 1,
            len(body.get(item_key, [])),
            bool(page_token),
        )
        if not page_token:
            break
    return items


async def get_channel_playlists(channel_id: str) -> list[dict[str, Any]]:
    """Fetch all playlists for a channel."""
    _consume_quota("global", 1)  # playlists.list = 1 unit
    return await _paginate(
        "/playlists",
        {"part": "snippet", "channelId": channel_id},
        max_pages=5,
        quota_per_page=1,
    )


async def get_playlist_items_paginated(
    playlist_id: str,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Fetch all items in a playlist with pageToken loop."""
    return await _paginate(
        "/playlistItems",
        {
            "part": "snippet",
            "playlistId": playlist_id,
        },
        max_pages=max_pages,
        quota_per_page=1,
    )


async def get_all_channel_videos_paginated(
    channel_id: str,
    order: str = "date",
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Fetch all videos for a channel via search.list with pageToken loop."""
    _consume_quota("global", 100)  # search.list = 100 units
    return await _paginate(
        "/search",
        {
            "part": "snippet",
            "channelId": channel_id,
            "order": order,
            "type": "video",
        },
        max_pages=max_pages,
        quota_per_page=100,
    )


def _extract_video_info(item: dict[str, Any]) -> dict[str, Any]:
    """Extract title, external_id, url, published_at from a YouTube API item."""
    snippet = item.get("snippet", {})
    resource_id = snippet.get("resourceId", {})
    video_id = resource_id.get("videoId") or ""

    # The 'id' field in search results is {videoId: X}, in playlistItems resourceId.videoId
    if isinstance(item.get("id"), dict):
        video_id = item["id"].get("videoId", "")
    if not video_id:
        video_id = snippet.get("resourceId", {}).get("videoId", "")

    published_at = None
    raw_pub = snippet.get("publishedAt")
    if raw_pub:
        try:
            published_at = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return {
        "external_id": video_id,
        "title": snippet.get("title", ""),
        "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
        "published_at": published_at,
    }


async def _insert_source_items(
    db: AsyncSession,
    source_id: str,
    items_data: list[dict[str, Any]],
    priority: int = 2,
) -> int:
    """Batch-insert source_items with ON CONFLICT DO NOTHING. Returns count inserted."""
    inserted_count = 0
    for data in items_data:
        video_id = data["external_id"]
        if not video_id:
            continue
        stmt = (
            insert(SourceItem)
            .values(
                source_id=source_id,
                external_id=video_id,
                title=data.get("title"),
                url=data.get("url"),
                published_at=data.get("published_at"),
                status="pending",
                priority=priority,
            )
            .on_conflict_do_nothing(index_elements=["source_id", "external_id"])
        )
        result = await db.execute(stmt)
        if result.rowcount and result.rowcount > 0:
            inserted_count += 1
    await db.commit()
    return inserted_count


async def poll_channel(
    source: Source,
    db: AsyncSession,
    backfill: bool = False,
) -> list[str]:
    """Fetch videos from a YouTube channel.

    In normal mode (backfill=False): only fetches videos published after
    last_checked_at (minus 2h buffer). Used by daily cron scheduler.

    In backfill mode: fetches all videos without date restriction.
    Used by manual "Scan Now" to backfill older content.

    Returns list of new video IDs found.
    """
    params: dict[str, Any] = {
        "part": "snippet",
        "channelId": source.external_id,
        "order": "date",
        "type": "video",
        "maxResults": 50,
    }
    # Only apply date filter in normal mode (not backfill)
    if source.last_checked_at and not backfill:
        since = source.last_checked_at - timedelta(hours=2)
        params["publishedAfter"] = since.isoformat()

    try:
        body = await _youtube_get("/search", params)
        _consume_quota(str(source.id), 100)  # search cost

        items = body.get("items", [])
        video_infos = [_extract_video_info(it) for it in items]
        video_infos = [v for v in video_infos if v["external_id"]]

        # Priority: 0 for videos published within 24h, 2 for older
        now_utc = datetime.now(timezone.utc)
        recent_threshold = now_utc - timedelta(hours=24)
        for v in video_infos:
            priority = 0 if (v["published_at"] and v["published_at"] >= recent_threshold) else 2
            v["_priority"] = priority

        # Batch insert with per-item priority
        inserted = 0
        for v in video_infos:
            result = await db.execute(
                insert(SourceItem)
                .values(
                    source_id=str(source.id),
                    external_id=v["external_id"],
                    title=v.get("title"),
                    url=v.get("url"),
                    published_at=v.get("published_at"),
                    status="pending",
                    priority=v.get("_priority", 2),
                )
                .on_conflict_do_nothing(index_elements=["source_id", "external_id"])
            )
            if result.rowcount and result.rowcount > 0:
                inserted += 1
        await db.commit()

        # Update source tracking timestamps
        source.last_checked_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info(
            "poll_channel %s: found %d videos, %d new inserted",
            source.name,
            len(video_infos),
            inserted,
        )
        return [v["external_id"] for v in video_infos]

    except YouTubeQuotaExceeded:
        logger.error("YouTube quota exceeded during poll_channel for %s", source.name)
        # Mark all pending YT items as rate_limited
        from sqlalchemy import update as sql_update

        await db.execute(
            sql_update(SourceItem)
            .where(
                SourceItem.source_id == source.id,
                SourceItem.status == "pending",
            )
            .values(status="rate_limited", error_message="YouTube daily quota exhausted")
        )
        await db.commit()
        raise


async def backfill_channel(
    source: Source,
    db: AsyncSession,
) -> dict[str, int]:
    """Initial ingestion for a new channel.
    Step 1: Fetch playlists and their items (priority=1)
    Step 2: Fetch all videos (priority=2), dedup against playlist items.
    """
    logger.info("Starting backfill for source %s", source.name)

    playlist_items_count = 0
    video_items_count = 0

    try:
        # Step 1: Playlists first
        playlists = await get_channel_playlists(source.external_id)
        logger.info("Found %d playlists for channel %s", len(playlists), source.name)

        seen_video_ids: set[str] = set()
        for pl in playlists:
            pl_id = pl.get("id", "")
            if not pl_id:
                continue
            try:
                items = await get_playlist_items_paginated(pl_id)
                video_infos = [_extract_video_info(it) for it in items]
                video_infos = [v for v in video_infos if v["external_id"]]
                count = await _insert_source_items(db, str(source.id), video_infos, priority=1)
                playlist_items_count += count
                seen_video_ids.update(v["external_id"] for v in video_infos)
            except YouTubeQuotaExceeded:
                logger.error("Quota exceeded during playlist backfill for %s", source.name)
                raise
            except Exception as exc:
                logger.warning("Failed to fetch playlist %s: %s", pl_id, exc)

        # Step 2: All videos (newest first)
        videos = await get_all_channel_videos_paginated(source.external_id, order="date", max_pages=20)
        video_infos = [_extract_video_info(it) for it in videos]
        video_infos = [v for v in video_infos if v["external_id"] and v["external_id"] not in seen_video_ids]
        count = await _insert_source_items(db, str(source.id), video_infos, priority=2)
        video_items_count = count

        # Update source timestamps
        source.last_checked_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info(
            "Backfill complete for %s: %d playlist items + %d videos",
            source.name,
            playlist_items_count,
            video_items_count,
        )
        return {"playlist_items": playlist_items_count, "video_items": video_items_count}

    except YouTubeQuotaExceeded:
        logger.error("YouTube quota exceeded during backfill for %s", source.name)
        # Mark pending items as rate_limited
        from sqlalchemy import update as sql_update

        await db.execute(
            sql_update(SourceItem)
            .where(
                SourceItem.source_id == source.id,
                SourceItem.status == "pending",
            )
            .values(status="rate_limited", error_message="YouTube daily quota exhausted")
        )
        await db.commit()
        raise
