"""Tracing wrapper for QueryRewriterPort implementations."""

from __future__ import annotations

import time

from llm_wiki.application.ports.search.query_rewriter_port import QueryRewriterPort
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan


class TracedQueryRewriterWrapper(QueryRewriterPort):
    """Wraps a query rewriter with telemetry spans under the pipeline root."""

    def __init__(
        self,
        inner: QueryRewriterPort,
        telemetry: TelemetryPort,
        parent_span: TelemetrySpan | None = None,
    ):
        self._inner = inner
        self._telemetry = telemetry
        self._parent_span = parent_span

    def set_parent_span(self, parent: TelemetrySpan) -> None:
        self._parent_span = parent

    async def rewrite(self, question: str, history: list[dict]) -> str:
        span = await self._telemetry.start_span(
            name="query_rewrite",
            kind="chain",
            inputs={
                "question_length": len(question),
                "history_turns": len(history),
            },
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            rewritten = await self._inner.rewrite(question, history)
            latency_ms = (time.time() - t0) * 1000
            was_rewritten = rewritten != question
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "rewritten": was_rewritten,
                    "output_length": len(rewritten),
                },
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "rewritten": was_rewritten,
                },
            )
            return rewritten
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
