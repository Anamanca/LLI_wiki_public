"""Force-reprocess completed wiki items through the v2 extraction pipeline.

Why: wiki-consumer's cached-page fast path (transcript_json['_wiki_page_id'])
skips Pass 1→2→3 entirely on retries, and the bulk restart admin route only
targets error statuses — so the 4,602 existing `completed` items would never
see quality improvements. This script selects completed items, clears ONLY the
cache marker, marks them `classified`, and re-pushes them into the wiki queue
(bounded batch, audited).

Usage:
    # dry run: list what WOULD be reprocessed (no DB writes, no queue)
    python scripts/reprocess-wiki.py --dry-run --limit 5

    # reprocess one canary item (clear marker + enqueue)
    python scripts/reprocess-wiki.py --external-id u45c_nVV0Sk

    # reprocess a bounded batch from a specific source
    python scripts/reprocess-wiki.py --source-id <uuid> --limit 20 --generation v2
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from uuid import UUID

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import select  # noqa: E402

from llm_wiki.infrastructure.persistence.postgres import models as orm  # noqa: E402
from llm_wiki.infrastructure.persistence.postgres.database import (  # noqa: E402
    async_session_factory,
)
from llm_wiki.infrastructure.persistence.redis.wiki_queue import push_wiki_job  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("reprocess-wiki")


async def _select_items(
    source_id: UUID | None,
    external_id: str | None,
    limit: int,
    generation: str,
) -> list[orm.SourceItem]:
    async with async_session_factory() as db:
        stmt = (
            select(orm.SourceItem)
            .where(orm.SourceItem.status == "completed")
            .order_by(orm.SourceItem.published_at.desc())
            .limit(limit)
        )
        if source_id:
            stmt = stmt.where(orm.SourceItem.source_id == source_id)
        if external_id:
            stmt = stmt.where(orm.SourceItem.external_id == external_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())


async def _run(
    source_id: UUID | None,
    external_id: str | None,
    limit: int,
    dry_run: bool,
    generation: str,
) -> int:
    items = await _select_items(source_id, external_id, limit, generation)
    logger.info(
        "Selected %d completed item(s)%s",
        len(items),
        " (DRY RUN — no changes)" if dry_run else "",
    )

    if dry_run:
        for item in items[:10]:
            marker = (item.transcript_json or {}).get("_wiki_page_id")
            logger.info(
                "  would reprocess: %s (%s) _wiki_page_id=%s",
                item.external_id,
                (item.title or "")[:50],
                marker,
            )
        return len(items)

    enqueued = 0
    async with async_session_factory() as db:
        for item in items:
            if not item.transcript_text:
                logger.warning("  skip %s: no transcript", item.external_id)
                continue
            data = item.transcript_json or {}
            # Bypass the cached-page fast path — the ONLY mutation we make.
            data.pop("_wiki_page_id", None)
            data["pipeline_generation"] = generation
            item.transcript_json = data
            item.status = "classified"
            item.retry_count = 0
            item.error_message = None
            item.started_at = None
            db.add(
                orm.IngestionLog(
                    source_item_id=item.id,
                    event_type="reprocess_v2",
                    message=f"force-reprocess generation={generation}",
                )
            )
        await db.commit()
        logger.info("Committed %d item(s) to 'classified'", len(items))

    for item in items:
        if not item.transcript_text:
            continue
        try:
            await push_wiki_job(item.id)
            enqueued += 1
        except Exception as exc:
            logger.error("  push failed for %s: %s", item.external_id, exc)

    logger.info("Enqueued %d wiki job(s) (batch=%d)", enqueued, len(items))
    return enqueued


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=UUID, default=None)
    parser.add_argument("--external-id", type=str, default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--generation", type=str, default="v2")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run and not args.external_id and args.limit > 100:
        parser.error("--limit > 100 requires explicit --external-id (canary) or --dry-run first")

    count = asyncio.run(
        _run(args.source_id, args.external_id, args.limit, args.dry_run, args.generation)
    )
    logger.info("Done: %d item(s)", count)


if __name__ == "__main__":
    main()
