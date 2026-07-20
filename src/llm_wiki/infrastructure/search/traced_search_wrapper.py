"""Tracing wrappers for vector and keyword search adapters."""

from __future__ import annotations

import time
from typing import Any

from llm_wiki.application.ports.search.vector_search import (
    KeywordSearchPort,
    VectorSearchPort,
)
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange


def _result_summary(results: list[SearchResult]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.content_id,
            "type": r.content_type,
            "title": r.title,
            "score": round(r.score, 4) if r.score is not None else None,
        }
        for r in results[:10]
    ]


class TracedVectorSearchWrapper(VectorSearchPort):
    """Wraps a vector search adapter with telemetry spans."""

    def __init__(
        self,
        inner: VectorSearchPort,
        telemetry: TelemetryPort,
        parent_span: TelemetrySpan | None = None,
    ):
        self._inner = inner
        self._telemetry = telemetry
        self._parent_span = parent_span

    async def search_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: str | None = None,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        return await self._trace(
            name="vector_search",
            method=self._inner.search_similar,
            embedding=embedding,
            top_k=top_k,
            source_id=source_id,
            time_range=time_range,
        )

    async def search_sections_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: str | None = None,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        return await self._trace(
            name="vector_search_sections",
            method=self._inner.search_sections_similar,
            embedding=embedding,
            top_k=top_k,
            source_id=source_id,
            time_range=time_range,
        )

    async def search_events_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        return await self._trace(
            name="vector_search_events",
            method=self._inner.search_events_similar,
            embedding=embedding,
            top_k=top_k,
            time_range=time_range,
        )

    async def _trace(
        self,
        name: str,
        method,
        embedding: Embedding,
        top_k: int,
        time_range: TimeRange | None,
        source_id: str | None = None,
    ) -> list[SearchResult]:
        span = await self._telemetry.start_span(
            name=name,
            kind="retriever",
            inputs={
                "embedding_dimensions": embedding.dimensions if embedding else None,
                "top_k": top_k,
                "source_id": source_id,
                "time_range": {
                    "start": time_range.start.isoformat() if time_range else None,
                    "end": time_range.end.isoformat() if time_range else None,
                },
            },
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            kwargs: dict[str, Any] = {
                "embedding": embedding,
                "top_k": top_k,
                "time_range": time_range,
            }
            if source_id is not None:
                kwargs["source_id"] = source_id
            results = await method(**kwargs)
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "result_count": len(results),
                    "results": _result_summary(results),
                },
            )
            await self._telemetry.add_metadata(
                span=span,
                metadata={"latency_ms": round(latency_ms, 2)},
            )
            return results
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "error_type": type(exc).__name__,
                },
            )
            await self._telemetry.end_span(span=span, error=str(exc))
            raise


class TracedKeywordSearchWrapper(KeywordSearchPort):
    """Wraps a keyword search adapter with telemetry spans."""

    def __init__(
        self,
        inner: KeywordSearchPort,
        telemetry: TelemetryPort,
        parent_span: TelemetrySpan | None = None,
    ):
        self._inner = inner
        self._telemetry = telemetry
        self._parent_span = parent_span

    async def search_keyword(
        self,
        query: str,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        span = await self._telemetry.start_span(
            name="keyword_search",
            kind="retriever",
            inputs={
                "query": query,
                "top_k": top_k,
                "time_range": {
                    "start": time_range.start.isoformat() if time_range else None,
                    "end": time_range.end.isoformat() if time_range else None,
                },
            },
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            results = await self._inner.search_keyword(
                query=query,
                top_k=top_k,
                time_range=time_range,
            )
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "result_count": len(results),
                    "results": _result_summary(results),
                },
            )
            await self._telemetry.add_metadata(
                span=span,
                metadata={"latency_ms": round(latency_ms, 2)},
            )
            return results
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "error_type": type(exc).__name__,
                },
            )
            await self._telemetry.end_span(span=span, error=str(exc))
            raise
