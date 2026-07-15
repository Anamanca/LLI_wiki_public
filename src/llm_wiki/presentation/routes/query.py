from typing import Optional
import time

from fastapi import APIRouter, Depends, HTTPException, Query as FastQuery
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse

from llm_wiki.presentation.dependencies import container, get_db
from llm_wiki.presentation.schemas.common import QueryRequest, QueryResponseModel
from llm_wiki.infrastructure.search.pgvector_adapter import PgVectorSearchAdapter
from llm_wiki.infrastructure.search.tsvector_adapter import TsVectorSearchAdapter
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline
from llm_wiki.application.use_cases.query.ask_question import AskQuestionUseCase
from llm_wiki.application.use_cases.query.stream_answer import StreamAnswerUseCase
from llm_wiki.application.dto.query_dto import QueryInput

router = APIRouter()


def get_query_pipeline(db: AsyncSession = Depends(get_db)):
    embedder = container.embedder()
    llm = container.llm_client()
    cache = container.cache()
    return QueryPipeline(
        embedder=embedder,
        vector_search=PgVectorSearchAdapter(db),
        keyword_search=TsVectorSearchAdapter(db),
        llm=llm,
        cache=cache,
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
    ))
    latency = (time.time() - t0) * 1000

    citations = [
        {
            "page_title": s.get("page_title") or s.get("title", ""),
            "page_slug": s.get("page_slug") or s.get("id", ""),
            "section": "",
            "source_name": "",
            "source_url": "",
            "timestamp": "",
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
                        "source_name": "",
                        "source_url": "",
                        "timestamp": "",
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
