"""Metrics collector singleton factory.

Follows the same pattern as ``telemetry/__init__.py``: module-level singleton
that resolves to a real or no-op adapter based on configuration.
"""

from __future__ import annotations

import logging

from llm_wiki.config import settings

logger = logging.getLogger(__name__)

_metrics = None


def get_metrics():
    """Return the configured metrics adapter singleton.

    First call creates the adapter; subsequent calls return the cached instance.
    When ``ENABLE_METRICS`` is false, a no-op adapter is returned so call sites
    never need to check the setting.
    """
    global _metrics
    if _metrics is not None:
        return _metrics

    enabled = getattr(settings, "enable_metrics", False)
    if not enabled:
        _metrics = _create_null_adapter()
        return _metrics

    try:
        _metrics = _create_prometheus_adapter()
        logger.info("Prometheus metrics adapter initialised")
    except Exception:
        logger.warning(
            "Failed to initialise Prometheus metrics adapter. Falling back to no-op adapter.",
            exc_info=True,
        )
        _metrics = _create_null_adapter()
    return _metrics


def _create_null_adapter():
    from llm_wiki.infrastructure.telemetry.null_metrics_adapter import NullMetricsAdapter

    return NullMetricsAdapter()


def _create_prometheus_adapter():
    from llm_wiki.infrastructure.telemetry.prometheus_metrics_adapter import (
        PrometheusMetricsAdapter,
    )

    return PrometheusMetricsAdapter()
