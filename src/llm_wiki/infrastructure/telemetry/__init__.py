"""Telemetry adapter factory."""

from __future__ import annotations

import logging

from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort
from llm_wiki.config import settings
from llm_wiki.infrastructure.telemetry.langsmith_telemetry_adapter import (
    LangSmithTelemetryAdapter,
)
from llm_wiki.infrastructure.telemetry.null_telemetry_adapter import NullTelemetryAdapter

logger = logging.getLogger(__name__)


def create_telemetry_adapter(
    enabled: bool | None = None,
    api_key: str | None = None,
    api_url: str | None = None,
    project: str | None = None,
) -> TelemetryPort:
    """Create the configured telemetry adapter.

    When tracing is disabled, missing the API key, or LangSmith is unreachable,
    the adapter degrades to a no-op implementation so the application continues
    to work normally.

    All LangSmith construction failures (import errors, network timeouts,
    invalid API keys) are caught and the adapter falls back to a no-op. This
    keeps the RAG pipeline running even when LangSmith is unavailable.
    """
    if enabled is None:
        enabled = getattr(settings, "langsmith_tracing_enabled", False)
    if api_key is None:
        api_key = getattr(settings, "langsmith_api_key", "")
    if api_url is None:
        api_url = getattr(settings, "langsmith_endpoint", "https://api.smith.langchain.com")
    if project is None:
        project = getattr(settings, "langsmith_project", "llm-wiki-rag")

    if not enabled or not api_key:
        return NullTelemetryAdapter()

    try:
        return LangSmithTelemetryAdapter(
            api_key=api_key,
            api_url=api_url,
            project_name=project,
        )
    except Exception:
        logger.warning(
            "Failed to initialize LangSmith telemetry adapter. "
            "Falling back to no-op tracer. Pipeline will operate normally.",
            exc_info=True,
        )
        return NullTelemetryAdapter()
