"""Telemetry port for tracing RAG pipeline execution.

This module defines the application-level abstraction used to emit spans and
metadata. It intentionally does not depend on any observability vendor SDK so
that domain and application layers stay vendor-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class TelemetrySpan:
    """A lightweight reference to an active telemetry span."""

    span_id: str
    name: str
    kind: str
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


class TelemetryPort(ABC):
    """Abstract port for emitting telemetry spans."""

    @abstractmethod
    async def start_span(
        self,
        name: str,
        kind: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        parent: TelemetrySpan | None = None,
    ) -> TelemetrySpan:
        """Start a new span and return a handle to it."""

    @abstractmethod
    async def end_span(
        self,
        span: TelemetrySpan,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """End a span, optionally recording outputs or an error message."""

    @abstractmethod
    async def add_metadata(self, span: TelemetrySpan, metadata: dict[str, Any]) -> None:
        """Attach extra metadata to an active span."""
