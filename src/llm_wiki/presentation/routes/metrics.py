"""Prometheus metrics endpoint.

Exposes aggregated application metrics in Prometheus text format at
GET /api/metrics. This endpoint is only registered when ENABLE_METRICS=true.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import PlainTextResponse

from llm_wiki.infrastructure.telemetry.metrics_collector import get_metrics

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Serve Prometheus metrics in exposition format."""
    data = get_metrics().get_metrics_response()
    return Response(content=data, media_type="text/plain; version=0.0.4")
