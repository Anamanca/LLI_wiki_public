"""Tracing wrapper for QueryAnalyzerPort implementations."""

from __future__ import annotations

import time

from llm_wiki.application.ports.search.query_analyzer_port import (
    QueryAnalysis,
    QueryAnalyzerPort,
)
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan


class TracedQueryAnalyzerWrapper(QueryAnalyzerPort):
    """Wraps a query analyzer with telemetry spans under the pipeline root."""

    def __init__(
        self,
        inner: QueryAnalyzerPort,
        telemetry: TelemetryPort,
        parent_span: TelemetrySpan | None = None,
    ):
        self._inner = inner
        self._telemetry = telemetry
        self._parent_span = parent_span

    def set_parent_span(self, parent: TelemetrySpan) -> None:
        self._parent_span = parent

    async def analyze(self, question: str) -> QueryAnalysis:
        span = await self._telemetry.start_span(
            name="query_analyze",
            kind="chain",
            inputs={"question_length": len(question)},
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            analysis = await self._inner.analyze(question)
            latency_ms = (time.time() - t0) * 1000
            time_range_info = (
                f"{analysis.time_range.start.isoformat()}"
                if analysis.time_range
                else None
            )
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "intent": analysis.intent,
                    "time_range": time_range_info,
                    "entities_count": len(analysis.entities),
                },
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "intent": analysis.intent,
                    "entities": analysis.entities[:10],
                },
            )
            return analysis
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.end_span(
                span=span,
                error=str(exc),
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "error_type": type(exc).__name__,
                },
            )
            raise
