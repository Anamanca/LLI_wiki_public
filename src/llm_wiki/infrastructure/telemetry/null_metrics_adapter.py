"""No-op metrics adapter used when metrics are disabled."""

from __future__ import annotations

from typing import Any


class NullMetricsAdapter:
    """Metrics adapter that does nothing.

    Used when ``ENABLE_METRICS`` is false so application code never needs to
    check whether metrics are active before recording.
    """

    def counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        return None

    def histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None

    def get_registry(self) -> Any:
        return None

    def get_metrics_response(self) -> bytes:
        return b""
