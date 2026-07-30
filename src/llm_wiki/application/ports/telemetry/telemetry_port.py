"""Telemetry port for tracing RAG pipeline execution.

This module defines the application-level abstraction used to emit spans and
metadata. It intentionally does not depend on any observability vendor SDK so
that domain and application layers stay vendor-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from llm_wiki.shared.datetime_utils import now


@dataclass(frozen=True)
class TelemetrySpan:
    """A lightweight reference to an active telemetry span."""

    span_id: str
    name: str
    kind: str
    start_time: datetime = field(default_factory=now)
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
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """End a span, optionally recording outputs, error, and extra metadata.

        *metadata* is merged into the span BEFORE the underlying run is
        finalized, guaranteeing it reaches the observability backend.  Callers
        should prefer this over separate ``add_metadata`` calls that race with
        ``end_span``.
        """

    @abstractmethod
    async def add_metadata(self, span: TelemetrySpan, metadata: dict[str, Any]) -> None:
        """Attach extra metadata to an active span."""
