from datetime import timedelta

from llm_wiki.shared.datetime_utils import now

from fastapi import APIRouter, Depends, Query as FastQuery
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.presentation.dependencies import container, get_db, traced_llm
from llm_wiki.infrastructure.persistence.postgres.repositories.event_repository import PostgresEventRepository
from llm_wiki.application.use_cases.query.summarize_time_range import (
    SummarizeTimeRangeUseCase,
    TimeRangeSummaryInput,
)

router = APIRouter()


@router.get("/summarize")
async def summarize_time_range(
    days: int = FastQuery(default=30, ge=1, le=365, description="Number of days to look back"),
    db: AsyncSession = Depends(get_db),
):
    now_ts = now()
    start = now_ts - timedelta(days=days)
    use_case = SummarizeTimeRangeUseCase(
        session=db,
        event_repo=PostgresEventRepository(db),
        llm=traced_llm("summarize_time_range"),
    )
    result = await use_case.execute(TimeRangeSummaryInput(
        start=start,
        end=now_ts,
    ))
    return {
        "summary": result.summary_text,
        "time_range": {
            "start": str(result.time_range.start),
            "end": str(result.time_range.end) if result.time_range.end else None,
        },
        "stats": {
            "event_count": result.event_count,
            "page_count": result.page_count,
            "items_completed": result.items_completed,
            "items_failed": result.items_failed,
            "items_rate_limited": result.items_rate_limited,
        },
        "top_events": result.top_events,
        "top_pages": result.top_pages,
    }
