"""No-op telemetry adapter used when tracing is disabled."""

from __future__ import annotations

from typing import Any

from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan


class NullTelemetryAdapter(TelemetryPort):
    """Telemetry adapter that does nothing.

    This is the default when ``LANGSMITH_TRACING`` is false or the API key is
    missing. It keeps the application code free of conditional tracing checks.
    """

    async def start_span(
        self,
        name: str,
        kind: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        parent: TelemetrySpan | None = None,
    ) -> TelemetrySpan:
        return TelemetrySpan(
            span_id="",
            name=name,
            kind=kind,
            metadata=metadata or {},
        )

    async def end_span(
        self,
        span: TelemetrySpan,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        return None

    async def add_metadata(self, span: TelemetrySpan, metadata: dict[str, Any]) -> None:
        return None
