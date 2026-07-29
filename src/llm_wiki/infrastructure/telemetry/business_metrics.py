"""Helper functions for common metric patterns.

All functions wrap the ``get_metrics()`` singleton so call sites never
touch Prometheus primitives directly. Every call is a safe no-op when
``ENABLE_METRICS=false``.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

from llm_wiki.infrastructure.telemetry.metrics_collector import get_metrics


@contextmanager
def track_duration(
    metric_name: str, labels: dict[str, str] | None = None
) -> Generator[None, None, None]:
    """Record a duration histogram for the wrapped block."""
    start = time.monotonic()
    try:
        yield
    finally:
        duration = time.monotonic() - start
        get_metrics().histogram(metric_name, duration, labels or {})


def inc_counter(
    metric_name: str, labels: dict[str, str] | None = None, value: float = 1
) -> None:
    """Increment a counter by *value* (default 1)."""
    get_metrics().counter(metric_name, value, labels or {})


def set_gauge(metric_name: str, value: float, labels: dict[str, str] | None = None) -> None:
    """Set a gauge to *value*."""
    get_metrics().gauge(metric_name, value, labels or {})
