"""RED (Rate-Errors-Duration) metrics middleware for FastAPI.

Records http_requests_total (counter) and http_request_duration_seconds
(histogram) per method + path + status. Path parameters are normalised
to avoid cardinality explosion (e.g. /api/sources/{uuid} → /api/sources/:id).
"""

from __future__ import annotations

import re
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from llm_wiki.infrastructure.telemetry.metrics_collector import get_metrics

# Patterns to normalise path parameters — avoid label cardinality explosion
_PATH_PATTERNS = [
    (re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "/:uuid"),
    (re.compile(r"/[0-9a-f]{24}"), "/:id"),
    (re.compile(r"/\d+"), "/:num"),
]


def _normalise_path(path: str) -> str:
    for pattern, replacement in _PATH_PATTERNS:
        path = pattern.sub(replacement, path)
    return path


class MetricsMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that records RED metrics for every HTTP request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.monotonic() - start
            metrics = get_metrics()
            path = _normalise_path(request.url.path)
            metrics.counter(
                "http_requests_total",
                1,
                {
                    "method": request.method,
                    "path": path,
                    "status": "500",
                },
            )
            metrics.histogram(
                "http_request_duration_seconds",
                elapsed,
                {
                    "method": request.method,
                    "path": path,
                },
            )
            raise

        elapsed = time.monotonic() - start
        metrics = get_metrics()
        path = _normalise_path(request.url.path)
        metrics.counter(
            "http_requests_total",
            1,
            {
                "method": request.method,
                "path": path,
                "status": str(response.status_code),
            },
        )
        metrics.histogram(
            "http_request_duration_seconds",
            elapsed,
            {
                "method": request.method,
                "path": path,
            },
        )
        return response
