import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.repositories.event_repository import EventRepository
from llm_wiki.application.ports.search.vector_search import LLMClientPort
from llm_wiki.domain.value_objects.time_range import TimeRange
from llm_wiki.infrastructure.persistence.postgres import models as orm

logger = logging.getLogger(__name__)


@dataclass
class TimeRangeSummaryInput:
    start: datetime
    end: datetime | None = None
    top_k: int = 10


@dataclass
class TimeRangeSummary:
    summary_text: str
    time_range: TimeRange
    event_count: int
    page_count: int
    items_completed: int
    items_failed: int
    items_rate_limited: int
    top_events: list[dict]
    top_pages: list[dict]


class SummarizeTimeRangeUseCase:
    def __init__(
        self,
        session: AsyncSession,
        event_repo: EventRepository,
        llm: LLMClientPort,
    ):
        self._session = session
        self._event_repo = event_repo
        self._llm = llm

    async def execute(self, input: TimeRangeSummaryInput) -> TimeRangeSummary:
        tr = TimeRange(start=input.start, end=input.end)

        events = await self._event_repo.list_by_date_range(
            start_date=input.start.date(),
            end_date=input.end.date() if input.end else None,
            limit=input.top_k * 2,
        )

        event_count_q = select(func.count(orm.EventCanonical.id)).where(
            orm.EventCanonical.normalized_date >= input.start.date()
        )
        if input.end:
            event_count_q = event_count_q.where(
                orm.EventCanonical.normalized_date <= input.end.date()
            )
        event_count = (await self._session.execute(event_count_q)).scalar() or 0

        pages_q = select(
            orm.Page.title,
            orm.Page.slug,
            orm.Page.published_at,
            orm.Page.summary,
        ).where(orm.Page.published_at >= input.start)
        if input.end:
            pages_q = pages_q.where(orm.Page.published_at <= input.end)
        pages_q = pages_q.order_by(orm.Page.published_at.desc()).limit(input.top_k)
        page_result = await self._session.execute(pages_q)
        page_rows = page_result.all()

        page_count_q = select(func.count(orm.Page.id)).where(orm.Page.published_at >= input.start)
        if input.end:
            page_count_q = page_count_q.where(orm.Page.published_at <= input.end)
        page_count = (await self._session.execute(page_count_q)).scalar() or 0

        items_q_base = select(orm.SourceItem).where(orm.SourceItem.started_at >= input.start)
        if input.end:
            items_q_base = items_q_base.where(orm.SourceItem.started_at <= input.end)

        completed_q = items_q_base.where(orm.SourceItem.status.in_(["completed", "published"]))
        completed_count = (
            await self._session.execute(select(func.count()).select_from(completed_q.subquery()))
        ).scalar() or 0

        failed_q = items_q_base.where(orm.SourceItem.status == "failed")
        failed_count = (
            await self._session.execute(select(func.count()).select_from(failed_q.subquery()))
        ).scalar() or 0

        rate_limited_q = items_q_base.where(orm.SourceItem.status == "rate_limited")
        rate_limited_count = (
            await self._session.execute(select(func.count()).select_from(rate_limited_q.subquery()))
        ).scalar() or 0

        top_events = [
            {
                "title": e.title,
                "date": str(e.normalized_date) if e.normalized_date else None,
                "summary": e.consensus_summary or "",
                "importance": e.importance_score,
            }
            for e in events[: input.top_k]
        ]

        top_pages = [
            {
                "title": r.title,
                "slug": r.slug,
                "published_at": str(r.published_at) if r.published_at else None,
                "summary": r.summary or "",
            }
            for r in page_rows[: input.top_k]
        ]

        context_parts = []
        if top_events:
            context_parts.append("## Key Events")
            for i, e in enumerate(top_events, 1):
                context_parts.append(f"[{i}] {e['title']} ({e['date']})\n{e['summary']}")
        if top_pages:
            context_parts.append("## Recent Pages")
            for i, p in enumerate(top_pages, len(top_events) + 1):
                context_parts.append(f"[{i}] {p['title']}\n{p['summary']}")

        stats = (
            f"Summary stats: {event_count} significant events, "
            f"{page_count} pages published, "
            f"{completed_count} items completed, "
            f"{failed_count} failed, {rate_limited_count} rate-limited."
        )

        time_str = str(input.start.date())
        if input.end:
            time_str += f" to {input.end.date()}"

        prompt = (
            f"You are an expert analyst. Write a concise executive summary of "
            f"the situation from {time_str} based on the data below. "
            f"Highlight 3-5 key takeaways. Use [N] citations. "
            f"Keep it under 500 words.\n\n{stats}\n\n" + "\n\n".join(context_parts)
        )

        try:
            summary_text = await self._llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1024,
            )
        except Exception:
            logger.warning("LLM summarization failed, using stats-only summary")
            summary_text = stats

        return TimeRangeSummary(
            summary_text=summary_text,
            time_range=tr,
            event_count=event_count,
            page_count=page_count,
            items_completed=completed_count,
            items_failed=failed_count,
            items_rate_limited=rate_limited_count,
            top_events=top_events,
            top_pages=top_pages,
        )
