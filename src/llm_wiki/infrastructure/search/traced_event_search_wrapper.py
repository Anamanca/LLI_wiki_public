"""Tracing wrapper for EventSearchPort implementations."""

from __future__ import annotations

import time
from typing import Any

from llm_wiki.application.ports.search.event_search_port import EventSearchPort
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange
from llm_wiki.infrastructure.search.traced_search_wrapper import _result_summary
from llm_wiki.infrastructure.telemetry.metrics_collector import get_metrics


class TracedEventSearchWrapper(EventSearchPort):
    """Wraps an event search adapter with telemetry spans."""

    def __init__(
        self,
        inner: EventSearchPort,
        telemetry: TelemetryPort,
        parent_span: TelemetrySpan | None = None,
    ):
        self._inner = inner
        self._telemetry = telemetry
        self._parent_span = parent_span

    def set_parent_span(self, parent: TelemetrySpan) -> None:
        self._parent_span = parent

    async def search_events(
        self,
        embedding: Embedding,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        span = await self._telemetry.start_span(
            name="event_search",
            kind="retriever",
            inputs={
                "embedding_dimensions": embedding.dimensions if embedding else None,
                "top_k": top_k,
                "time_range": (
                    f"{time_range.start.isoformat()}→{time_range.end.isoformat()}"
                    if time_range
                    else None
                ),
            },
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            results = await self._inner.search_events(embedding, top_k, time_range)
            latency_s = time.time() - t0
            get_metrics().histogram("event_search_duration_seconds", latency_s)
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "result_count": len(results),
                    "results": _result_summary(results),
                },
                metadata={
                    "latency_ms": round(latency_s * 1000, 2),
                    "top_score": round(results[0].score, 4) if results else 0.0,
                },
            )
            return results
        except Exception as exc:
            latency_s = time.time() - t0
            await self._telemetry.end_span(
                span=span,
                error=str(exc),
                metadata={
                    "latency_ms": round(latency_s * 1000, 2),
                    "error_type": type(exc).__name__,
                },
            )
            raise

    async def search_events_keyword(
        self,
        query: str,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        span = await self._telemetry.start_span(
            name="event_keyword_search",
            kind="retriever",
            inputs={
                "query": query,
                "top_k": top_k,
                "time_range": (
                    f"{time_range.start.isoformat()}→{time_range.end.isoformat()}"
                    if time_range
                    else None
                ),
            },
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            results = await self._inner.search_events_keyword(query, top_k, time_range)
            latency_s = time.time() - t0
            get_metrics().histogram("event_keyword_search_duration_seconds", latency_s)
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "result_count": len(results),
                    "results": _result_summary(results),
                },
                metadata={
                    "latency_ms": round(latency_s * 1000, 2),
                    "top_score": round(results[0].score, 4) if results else 0.0,
                },
            )
            return results
        except Exception as exc:
            latency_s = time.time() - t0
            await self._telemetry.end_span(
                span=span,
                error=str(exc),
                metadata={
                    "latency_ms": round(latency_s * 1000, 2),
                    "error_type": type(exc).__name__,
                },
            )
            raise
