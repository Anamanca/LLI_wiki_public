"""YouTube Data API v3 client — poll channel for new videos, backfill playlists/videos.

Daily scan strategy:
  1. channels.list  (1 unit)  → get uploads playlist ID
  2. playlistItems.list (1 unit/page) → paginate newest-first, stop when
     we reach videos older than source.last_video_published_at
  Total: ~2-5 units per channel (vs 100 units with search.list).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.config import settings
from llm_wiki.infrastructure.persistence.postgres.models import ScanLog, Source, SourceItem
from llm_wiki.shared.datetime_utils import now

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Safety cap for first-time / backfill scans — prevents runaway pagination.
MAX_BACKFILL_PAGES = 50  # 50 pages × 50 items = 2500 videos
MAX_DAILY_PAGES = 10  # 10 pages × 50 items = 500 videos


# ── Quota tracking (module-level, reset on process restart) ────────────────
_quota_used: dict[str, int] = {}  # source_id -> units used today


def _consume_quota(source_id: str, units: int) -> None:
    _quota_used[source_id] = _quota_used.get(source_id, 0) + units


# ── Exceptions ──────────────────────────────────────────────────────────────


class YouTubeQuotaExceeded(Exception):
    """Raised when YouTube returns 403 quotaExceeded."""

    pass


# ── Result types ────────────────────────────────────────────────────────────


@dataclass
class PollResult:
    """Returned by poll_channel — consumed by admin.py for scan_logs."""

    video_ids: list[str] = field(default_factory=list)
    found: int = 0
    inserted: int = 0
    api_calls: int = 0
    quota_used: int = 0
    error: str | None = None


# ── Helpers ─────────────────────────────────────────────────────────────────


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


# ── Cheap daily poll: channels.list → playlistItems.list ────────────────────


async def _get_uploads_playlist_id(channel_id: str) -> str:
    """Get the channel's uploads playlist ID. Cost: 1 unit."""
    body = await _youtube_get(
        "/channels",
        {
            "part": "contentDetails",
            "id": channel_id,
        },
    )
    items = body.get("items", [])
    if not items:
        raise RuntimeError(f"Channel not found: {channel_id}")
    playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    logger.debug("uploads playlist for %s: %s", channel_id, playlist_id)
    return playlist_id


async def _poll_uploads_playlist(
    playlist_id: str,
    since: datetime | None,
    max_pages: int = MAX_DAILY_PAGES,
) -> tuple[list[dict[str, Any]], int]:
    """Paginate playlistItems.list (reverse-chrono), stop when past `since`.

    Returns (items published after since, number of API calls made).
    Each page costs 1 quota unit.
    """
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    api_calls = 0

    # Buffer to avoid missing videos near the boundary (clock skew, API delay).
    buffer = timedelta(hours=4)

    for page_num in range(max_pages):
        params: dict[str, Any] = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token

        body = await _youtube_get("/playlistItems", params)
        api_calls += 1
        page_items = body.get("items", [])

        if not page_items:
            break  # empty playlist

        # Check stop condition on the oldest item of this page.
        if since is not None:
            oldest = page_items[-1]
            oldest_pub_str = oldest.get("snippet", {}).get("publishedAt")
            if oldest_pub_str:
                try:
                    oldest_pub = datetime.fromisoformat(oldest_pub_str.replace("Z", "+00:00"))
                    if oldest_pub < (since - buffer):
                        # This page spans past our checkpoint — include only
                        # items newer than since and stop.
                        for it in page_items:
                            pub_str = it.get("snippet", {}).get("publishedAt")
                            if pub_str:
                                try:
                                    pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                                    if pub >= (since - buffer):
                                        items.append(it)
                                except (ValueError, TypeError):
                                    items.append(it)
                        logger.debug(
                            "_poll_uploads: stopping at page %d (oldest=%s < since=%s)",
                            page_num + 1,
                            oldest_pub_str,
                            since.isoformat(),
                        )
                        return items, api_calls
                except (ValueError, TypeError):
                    pass  # can't parse — keep going

        items.extend(page_items)
        page_token = body.get("nextPageToken")
        if not page_token:
            break

    # If no since or we exhausted the playlist, filter by since here.
    if since is not None:
        items = [it for it in items if _item_published_after(it, since - buffer)]

    return items, api_calls


def _item_published_after(item: dict[str, Any], threshold: datetime) -> bool:
    """True if the item's publishedAt is >= threshold."""
    pub_str = item.get("snippet", {}).get("publishedAt")
    if not pub_str:
        return True  # no date → include to be safe
    try:
        pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        return pub >= threshold
    except (ValueError, TypeError):
        return True


# ── Parsing ─────────────────────────────────────────────────────────────────


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


# ── DB helpers ──────────────────────────────────────────────────────────────


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


async def write_scan_log(
    db: AsyncSession,
    source_id: str,
    source_name: str,
    scan_type: str,
    result: PollResult,
) -> None:
    """Insert a scan_logs row for this source scan."""
    log = ScanLog(
        source_id=source_id,
        source_name=source_name,
        scan_type=scan_type,
        started_at=now(),
        completed_at=now(),
        api_calls=result.api_calls,
        quota_used=result.quota_used,
        videos_found=result.found,
        videos_inserted=result.inserted,
        error_message=result.error,
        success=result.error is None,
    )
    db.add(log)
    await db.commit()


async def _mark_source_items_rate_limited(source_id: str, db: AsyncSession) -> None:
    """Mark all pending items for this source as rate_limited."""
    await db.execute(
        sql_update(SourceItem)
        .where(
            SourceItem.source_id == source_id,
            SourceItem.status == "pending",
        )
        .values(status="rate_limited", error_message="YouTube daily quota exhausted")
    )
    await db.commit()


# ── Main entry points ───────────────────────────────────────────────────────


async def poll_channel(
    source: Source,
    db: AsyncSession,
    backfill: bool = False,
) -> PollResult:
    """Fetch new videos from a YouTube channel using cheap playlistItems API.

    Daily mode (backfill=False):
      - Uses channels.list (1u) + playlistItems.list (1u/page)
      - Queries only for videos published after source.last_video_published_at
      - Updates source.last_video_published_at to the newest found
      - If last_video_published_at is NULL (first scan), paginates up to
        MAX_DAILY_PAGES without a stop condition.

    Backfill mode (backfill=True):
      - Same cheap approach but without since filter (full pagination up to
        MAX_BACKFILL_PAGES).

    Returns PollResult with counts and quota info.
    """
    result = PollResult()
    channel_id = source.external_id

    try:
        # Step 1: Get uploads playlist ID (1 unit)
        playlist_id = await _get_uploads_playlist_id(channel_id)
        result.api_calls += 1
        result.quota_used += 1  # channels.list = 1 unit

        # Step 2: Determine the cutoff point
        since: datetime | None = None
        if source.last_video_published_at and not backfill:
            since = source.last_video_published_at
        elif source.last_video_published_at and backfill:
            # Backfill: no since filter, but still paginate from newest
            since = None
        else:
            # First scan or backfill — no since, catch up with cap
            since = None

        # Step 3: Paginate playlist items
        max_pages = MAX_BACKFILL_PAGES if (backfill or since is None) else MAX_DAILY_PAGES
        playlist_items, api_calls = await _poll_uploads_playlist(
            playlist_id,
            since,
            max_pages=max_pages,
        )
        result.api_calls += api_calls
        result.quota_used += api_calls  # playlistItems.list = 1 unit per page

        # Step 4: Extract video info & deduplicate
        video_infos = [_extract_video_info(it) for it in playlist_items]
        video_infos = [v for v in video_infos if v["external_id"]]
        result.found = len(video_infos)

        if not video_infos:
            # No new videos — still update last_checked_at.
            source.last_checked_at = now()
            await db.commit()
            logger.info("poll_channel %s: no new videos found", source.name)
            return result

        # Step 5: Assign priority & insert
        now_utc = now()
        recent_threshold = now_utc - timedelta(hours=24)

        inserted = 0
        newest_published_at: datetime | None = None

        for v in video_infos:
            priority = 0 if (v["published_at"] and v["published_at"] >= recent_threshold) else 2
            db_result = await db.execute(
                insert(SourceItem)
                .values(
                    source_id=str(source.id),
                    external_id=v["external_id"],
                    title=v.get("title"),
                    url=v.get("url"),
                    published_at=v.get("published_at"),
                    status="pending",
                    priority=priority,
                )
                .on_conflict_do_nothing(index_elements=["source_id", "external_id"])
            )
            if db_result.rowcount and db_result.rowcount > 0:
                inserted += 1
            if v["published_at"] and (
                newest_published_at is None or v["published_at"] > newest_published_at
            ):
                newest_published_at = v["published_at"]

        await db.commit()
        result.inserted = inserted

        # Step 6: Update source tracking timestamps
        source.last_checked_at = now()
        if newest_published_at and (
            source.last_video_published_at is None
            or newest_published_at > source.last_video_published_at
        ):
            source.last_video_published_at = newest_published_at
        await db.commit()

        logger.info(
            "poll_channel %s: found %d, new %d, quota %d units",
            source.name,
            result.found,
            result.inserted,
            result.quota_used,
        )
        result.video_ids = [v["external_id"] for v in video_infos]
        return result

    except YouTubeQuotaExceeded:
        logger.error("YouTube quota exceeded during poll_channel for %s", source.name)
        result.error = "YouTube daily quota exhausted"
        await _mark_source_items_rate_limited(str(source.id), db)
        return result
    except Exception as exc:
        logger.exception("poll_channel %s failed: %s", source.name, exc)
        result.error = str(exc)[:500]
        return result


# ── Legacy backfill (playlist + video full scan) ────────────────────────────
# These functions are used by backfill_channel() for manual "Scan Now" runs.


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
        videos = await get_all_channel_videos_paginated(
            source.external_id, order="date", max_pages=20
        )
        video_infos = [_extract_video_info(it) for it in videos]
        video_infos = [
            v for v in video_infos if v["external_id"] and v["external_id"] not in seen_video_ids
        ]
        count = await _insert_source_items(db, str(source.id), video_infos, priority=2)
        video_items_count = count

        # Update source timestamps
        source.last_checked_at = now()

        # Set last_video_published_at to newest video across all ingested items
        newest = await db.execute(
            select(SourceItem.published_at)
            .where(SourceItem.source_id == str(source.id))
            .order_by(SourceItem.published_at.desc().nullslast())
            .limit(1)
        )
        newest_pub = newest.scalar()
        if newest_pub:
            source.last_video_published_at = newest_pub

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
        await _mark_source_items_rate_limited(str(source.id), db)
        raise
