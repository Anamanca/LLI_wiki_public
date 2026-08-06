"""Tracing wrapper for GuardrailAnalyzerPort implementations."""

from __future__ import annotations

import time

from llm_wiki.application.ports.search.guardrail_analyzer_port import (
    GuardrailAnalysis,
    GuardrailAnalyzerPort,
)
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan


class TracedGuardrailAnalyzerWrapper(GuardrailAnalyzerPort):
    """Wraps a guardrail analyzer with telemetry spans under the pipeline root."""

    def __init__(
        self,
        inner: GuardrailAnalyzerPort,
        telemetry: TelemetryPort,
        parent_span: TelemetrySpan | None = None,
    ):
        self._inner = inner
        self._telemetry = telemetry
        self._parent_span = parent_span

    def set_parent_span(self, parent: TelemetrySpan) -> None:
        self._parent_span = parent

    async def analyze(self, question: str) -> GuardrailAnalysis:
        span = await self._telemetry.start_span(
            name="query_analyze",
            kind="chain",
            inputs={"question": question},
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
                    "allowed": analysis.allowed,
                    "reason": analysis.reason if not analysis.allowed else None,
                    "intent": analysis.intent,
                    "language": analysis.language,
                    "time_range": time_range_info,
                    "entities_count": len(analysis.entities),
                    "sub_questions_count": len(analysis.sub_questions),
                },
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "allowed": analysis.allowed,
                    "intent": analysis.intent,
                    "language": analysis.language,
                    "entities": analysis.entities[:10],
                    "embedding_text": (
                        analysis.embedding_text[:200] if analysis.embedding_text else ""
                    ),
                    "page_search_query": analysis.page_search_query,
                    "event_search_query": analysis.event_search_query,
                    "sub_questions": analysis.sub_questions,
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
