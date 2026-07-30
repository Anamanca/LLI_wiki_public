import logging
from typing import Optional
import time
from datetime import datetime, timedelta

from llm_wiki.shared.datetime_utils import now

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query as FastQuery
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse

from llm_wiki.presentation.dependencies import container, get_db
from llm_wiki.presentation.schemas.common import QueryRequest, QueryResponseModel
from llm_wiki.infrastructure.search.pgvector_adapter import PgVectorSearchAdapter
from llm_wiki.infrastructure.search.tsvector_adapter import TsVectorSearchAdapter
from llm_wiki.infrastructure.search.event_search_adapter import PgVectorEventSearchAdapter
from llm_wiki.infrastructure.search.graph_rag_adapter import PostgresGraphRAGAdapter
from llm_wiki.infrastructure.search.traced_search_wrapper import (
    TracedKeywordSearchWrapper,
    TracedVectorSearchWrapper,
)
from llm_wiki.infrastructure.search.traced_event_search_wrapper import TracedEventSearchWrapper
from llm_wiki.infrastructure.llm.traced_llm_wrapper import TracedLLMWrapper
from llm_wiki.infrastructure.llm.traced_query_rewriter_wrapper import TracedQueryRewriterWrapper
from llm_wiki.infrastructure.llm.traced_query_analyzer_wrapper import TracedQueryAnalyzerWrapper
from llm_wiki.infrastructure.llm.query_rewriter_adapter import LLMQueryRewriterAdapter
from llm_wiki.infrastructure.llm.query_analyzer_adapter import LLMQueryAnalyzerAdapter
from llm_wiki.infrastructure.llm.query_expander_adapter import LLMQueryExpanderAdapter
from llm_wiki.infrastructure.llm.reranker_adapter import LLMRerankerAdapter
from llm_wiki.infrastructure.search.cross_encoder_reranker_adapter import (
    CrossEncoderRerankerAdapter,
)
from llm_wiki.infrastructure.llm.answer_evaluator_adapter import LLMAnswerEvaluatorAdapter
from llm_wiki.infrastructure.embedding.traced_embedding_wrapper import TracedEmbeddingWrapper
from llm_wiki.infrastructure.persistence.redis.traced_cache_wrapper import TracedCacheWrapper
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline
from llm_wiki.application.use_cases.query.ask_question import AskQuestionUseCase
from llm_wiki.application.use_cases.query.stream_answer import StreamAnswerUseCase
from llm_wiki.application.use_cases.query.reflective_pipeline import (
    SelfReflectiveRAGPipeline,
    SelfReflectiveAskQuestionUseCase,
    SelfReflectiveStreamAnswerUseCase,
)
from llm_wiki.application.use_cases.query.summarize_time_range import (
    SummarizeTimeRangeUseCase,
    TimeRangeSummaryInput,
)
from llm_wiki.infrastructure.persistence.postgres.repositories.event_repository import PostgresEventRepository
from llm_wiki.application.dto.query_dto import QueryInput

router = APIRouter()


def _build_adapters(db: AsyncSession):
    """Build all adapters for the RAG pipeline.

    Returns a dict so callers can pick the pipeline variant:
    - ``standard`` → QueryPipeline (original)
    - ``reflective`` → SelfReflectiveRAGPipeline (Phase 3)
    """
    telemetry = container.telemetry()
    llm_raw = container.llm_client()

    embedder = TracedEmbeddingWrapper(
        container.embedder(), telemetry,
        model=container.config.langsmith_evaluator_model() or "unknown",
    )
    llm = TracedLLMWrapper(
        llm_raw, telemetry,
        model=container.config.opencode_primary_model() or "unknown",
    )
    cache = TracedCacheWrapper(container.cache(), telemetry)

    # Search adapters use default recency_lambda — the pipeline overrides
    # per-intent by creating new adapters with intent-specific lambda when
    # the reflective pipeline detects the intent.
    vector_search = TracedVectorSearchWrapper(PgVectorSearchAdapter(db), telemetry)
    keyword_search = TracedKeywordSearchWrapper(TsVectorSearchAdapter(db), telemetry)
    event_search = TracedEventSearchWrapper(PgVectorEventSearchAdapter(db), telemetry)

    rewriter = TracedQueryRewriterWrapper(
        LLMQueryRewriterAdapter(llm_raw), telemetry,
    )
    analyzer = TracedQueryAnalyzerWrapper(
        LLMQueryAnalyzerAdapter(llm_raw), telemetry,
    )

    # Phase 2 new ports
    graph_rag = PostgresGraphRAGAdapter(db)
    expander = LLMQueryExpanderAdapter(llm_raw)

    # Reranker: prefer local cross-encoder when enabled, fall back to LLM-based
    from llm_wiki.config import settings
    if settings.cross_encoder_enabled:
        re_ranker = CrossEncoderRerankerAdapter(
            model_name=settings.cross_encoder_model,
        )
        logger.info("Configured reranker: cross-encoder (model=%s)", settings.cross_encoder_model)
    else:
        re_ranker = LLMRerankerAdapter(llm_raw)
        logger.info("Configured reranker: LLM-based")

    evaluator = LLMAnswerEvaluatorAdapter(llm_raw)

    standard_pipeline = QueryPipeline(
        embedder=embedder,
        vector_search=vector_search,
        keyword_search=keyword_search,
        llm=llm,
        cache=cache,
        telemetry=telemetry,
        rewriter=rewriter,
        analyzer=analyzer,
        event_search=event_search,
        graph_rag=graph_rag,
    )

    reflective_pipeline = SelfReflectiveRAGPipeline(
        base_pipeline=standard_pipeline,
        evaluator=evaluator,
        expander=expander,
        re_ranker=re_ranker,
        graph_rag=graph_rag,
        llm=llm,
        embedder=embedder,
        vector_search=vector_search,
        keyword_search=keyword_search,
        event_search=event_search,
        cache=cache,
        telemetry=telemetry,
        rewriter=rewriter,
        analyzer=analyzer,
    )

    return {
        "standard": standard_pipeline,
        "reflective": reflective_pipeline,
    }


@router.post("/query", response_model=QueryResponseModel)
async def ask_question(
    payload: QueryRequest,
    db: AsyncSession = Depends(get_db),
):
    t0 = time.time()
    adapters = _build_adapters(db)

    # Select pipeline based on config
    from llm_wiki.config import settings
    if settings.reasoning_enabled:
        pipeline = adapters["reflective"]
    else:
        pipeline = adapters["standard"]

    query_input = QueryInput(
        question=payload.question,
        source_id=payload.source_id,
        top_k=payload.top_k or 25,  # Phase 1: increased from 10 → 25
        chat_history=[{"role": m.role, "content": m.content} for m in (payload.history or [])],
        from_date=payload.from_date,
        to_date=payload.to_date,
    )

    # Use reflective pipeline if reasoning is enabled, else standard
    if isinstance(pipeline, SelfReflectiveRAGPipeline):
        result = await pipeline.execute(query_input)
    else:
        use_case = AskQuestionUseCase(pipeline)
        result = await use_case.execute(query_input)
        result = {"answer": result.answer, "sources": result.sources,
                  "tokens_used": result.tokens_used, "cache_hit": result.cache_hit,
                  "pipeline_steps": result.pipeline_steps}

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
        for s in result.get("sources", [])
    ]

    sources_used = []
    seen_names = set()
    for s in result.get("sources", []):
        name = s.get("source_name") or s.get("title", "unknown")
        if name not in seen_names:
            seen_names.add(name)
            sources_used.append({"name": name, "pages_used": 1})

    return QueryResponseModel(
        answer=result.get("answer", ""),
        citations=citations,
        sources_used=sources_used,
        tokens_used=result.get("tokens_used", 0),
        latency_ms=round(latency, 2),
    )


@router.post("/query/stream")
async def ask_question_stream(
    payload: QueryRequest,
    db: AsyncSession = Depends(get_db),
):
    adapters = _build_adapters(db)

    from llm_wiki.config import settings
    if settings.reasoning_enabled:
        pipeline = adapters["reflective"]
    else:
        pipeline = adapters["standard"]

    query_input = QueryInput(
        question=payload.question,
        source_id=payload.source_id,
        top_k=payload.top_k or 25,  # Phase 1: increased from 10 → 25
        stream=True,
        chat_history=[{"role": m.role, "content": m.content} for m in (payload.history or [])],
        from_date=payload.from_date,
        to_date=payload.to_date,
    )

    async def event_stream():
        if isinstance(pipeline, SelfReflectiveRAGPipeline):
            use_case = SelfReflectiveStreamAnswerUseCase(pipeline)
        else:
            use_case = StreamAnswerUseCase(pipeline)

        async for chunk in use_case.execute(query_input):
            import json

            chunk_type = chunk.get("type", "")
            chunk_data = chunk.get("data")

            if chunk_type == "status":
                yield f"data: {json.dumps({'type': 'status', 'status': chunk_data.get('status') if isinstance(chunk_data, dict) else chunk_data})}\n\n"
            elif chunk_type == "complete":
                citations = [
                    {
                        "page_title": s.get("page_title") or s.get("title", ""),
                        "page_slug": s.get("page_slug") or s.get("id", ""),
                        "section": "",
                        "source_name": s.get("source_name") or "",
                        "source_url": "",
                        "timestamp": s.get("published_at") or "",
                    }
                    for s in (chunk_data.get("citations", []) if isinstance(chunk_data, dict) else [])
                ]
                yield f"data: {json.dumps({'type': 'complete', 'answer': chunk_data.get('answer') if isinstance(chunk_data, dict) else '', 'citations': citations, 'sources_used': chunk_data.get('sources_used', []) if isinstance(chunk_data, dict) else []})}\n\n"
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
