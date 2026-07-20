from typing import Optional
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query as FastQuery
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse

from llm_wiki.presentation.dependencies import container, get_db
from llm_wiki.presentation.schemas.common import QueryRequest, QueryResponseModel
from llm_wiki.infrastructure.search.pgvector_adapter import PgVectorSearchAdapter
from llm_wiki.infrastructure.search.tsvector_adapter import TsVectorSearchAdapter
from llm_wiki.infrastructure.search.traced_search_wrapper import (
    TracedKeywordSearchWrapper,
    TracedVectorSearchWrapper,
)
from llm_wiki.infrastructure.llm.traced_llm_wrapper import TracedLLMWrapper
from llm_wiki.infrastructure.embedding.traced_embedding_wrapper import TracedEmbeddingWrapper
from llm_wiki.infrastructure.persistence.redis.traced_cache_wrapper import TracedCacheWrapper
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline
from llm_wiki.application.use_cases.query.ask_question import AskQuestionUseCase
from llm_wiki.application.use_cases.query.stream_answer import StreamAnswerUseCase
from llm_wiki.application.use_cases.query.summarize_time_range import (
    SummarizeTimeRangeUseCase,
    TimeRangeSummaryInput,
)
from llm_wiki.infrastructure.persistence.postgres.repositories.event_repository import PostgresEventRepository
from llm_wiki.application.dto.query_dto import QueryInput

router = APIRouter()


def get_query_pipeline(db: AsyncSession = Depends(get_db)):
    telemetry = container.telemetry()
    embedder = TracedEmbeddingWrapper(
        container.embedder(),
        telemetry,
        model=container.config.langsmith_evaluator_model() or "unknown",
    )
    llm = TracedLLMWrapper(
        container.llm_client(),
        telemetry,
        model=container.config.opencode_primary_model() or "unknown",
    )
    cache = TracedCacheWrapper(container.cache(), telemetry)
    vector_search = TracedVectorSearchWrapper(PgVectorSearchAdapter(db), telemetry)
    keyword_search = TracedKeywordSearchWrapper(TsVectorSearchAdapter(db), telemetry)
    return QueryPipeline(
        embedder=embedder,
        vector_search=vector_search,
        keyword_search=keyword_search,
        llm=llm,
        cache=cache,
        telemetry=telemetry,
    )


@router.post("/query", response_model=QueryResponseModel)
async def ask_question(
    payload: QueryRequest,
    pipeline: QueryPipeline = Depends(get_query_pipeline),
):
    t0 = time.time()
    use_case = AskQuestionUseCase(pipeline)
    result = await use_case.execute(QueryInput(
        question=payload.question,
        source_id=payload.source_id,
        top_k=payload.top_k or 10,
        from_date=payload.from_date,
        to_date=payload.to_date,
    ))
    latency = (time.time() - t0) * 1000

    citations = [
        {
            "page_title": s.get("page_title") or s.get("title", ""),
            "page_slug": s.get("page_slug") or s.get("id", ""),
            "section": "",
            "source_name": s.get("source_name") or "",
            "source_url": "",
            "timestamp": s.get("published_at") or "",
        }
        for s in result.sources
    ]

    sources_used = []
    seen_names = set()
    for s in result.sources:
        name = s.get("source_name") or s.get("title", "unknown")
        if name not in seen_names:
            seen_names.add(name)
            sources_used.append({"name": name, "pages_used": 1})

    return QueryResponseModel(
        answer=result.answer,
        citations=citations,
        sources_used=sources_used,
        tokens_used=result.tokens_used,
        latency_ms=round(latency, 2),
    )


@router.post("/query/stream")
async def ask_question_stream(
    payload: QueryRequest,
    pipeline: QueryPipeline = Depends(get_query_pipeline),
):
    use_case = StreamAnswerUseCase(pipeline)
    async def event_stream():
        async for chunk in use_case.execute(QueryInput(
            question=payload.question,
            source_id=payload.source_id,
            top_k=payload.top_k or 10,
            stream=True,
            from_date=payload.from_date,
            to_date=payload.to_date,
        )):
            import json

            chunk_type = chunk.get("type", "")
            chunk_data = chunk.get("data")

            if chunk_type == "metadata":
                yield f"data: {json.dumps({'type': 'metadata', 'pipeline_steps': chunk_data.get('pipeline_steps', {})})}\n\n"
            elif chunk_type == "chunk":
                # Frontend expects: {type: "token", content: "..."}
                if chunk_data is None:
                    continue
                content = chunk_data if isinstance(chunk_data, str) else (chunk_data.get("content") or "")
                yield f"data: {json.dumps({'type': 'token', 'content': content})}\n\n"
            elif chunk_type == "sources":
                citations = [
                    {
                        "page_title": s.get("page_title") or s.get("title", ""),
                        "page_slug": s.get("page_slug") or s.get("id", ""),
                        "section": "",
                        "source_name": s.get("source_name") or "",
                        "source_url": "",
                        "timestamp": s.get("published_at") or "",
                    }
                    for s in (chunk_data if isinstance(chunk_data, list) else [])
                ]
                yield f"data: {json.dumps({'type': 'complete', 'citations': citations, 'sources_used': []})}\n\n"
            elif chunk_type == "done":
                yield f"data: {json.dumps({'type': 'complete', 'citations': [], 'sources_used': []})}\n\n"
            else:
                yield f"data: {json.dumps(chunk, default=str)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/summarize")
async def summarize_time_range(
    days: int = FastQuery(default=30, ge=1, le=365, description="Number of days to look back"),
    db: AsyncSession = Depends(get_db),
):
    from llm_wiki.presentation.dependencies import traced_llm
    now = datetime.utcnow()
    start = now - timedelta(days=days)
    use_case = SummarizeTimeRangeUseCase(
        session=db,
        event_repo=PostgresEventRepository(db),
        llm=traced_llm("summarize_time_range"),
    )
    result = await use_case.execute(TimeRangeSummaryInput(
        start=start,
        end=now,
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
