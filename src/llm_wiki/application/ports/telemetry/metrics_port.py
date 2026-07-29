"""Metrics port for recording application and business metrics.

This module defines the application-level abstraction for emitting counters,
histograms, and gauges. It intentionally does not depend on any observability
vendor SDK so that domain and application layers stay vendor-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MetricsPort(ABC):
    """Abstract port for emitting Prometheus-style metrics."""

    @abstractmethod
    def counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment a counter by *value* (default 1)."""

    @abstractmethod
    def histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Record an observation in a histogram."""

    @abstractmethod
    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge to *value*."""

    @abstractmethod
    def get_registry(self) -> Any:
        """Return the underlying metrics registry (implementation-specific)."""

    @abstractmethod
    def get_metrics_response(self) -> bytes:
        """Return the serialised metrics payload for the /metrics endpoint."""
