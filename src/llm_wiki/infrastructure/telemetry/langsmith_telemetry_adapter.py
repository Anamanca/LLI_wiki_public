"""LangSmith-backed telemetry adapter."""

from __future__ import annotations

import contextvars
import logging
from typing import Any
from uuid import uuid4

from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan

logger = logging.getLogger(__name__)

_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_trace_id", default=None
)
_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_span_id", default=None
)


def get_current_trace_id() -> str | None:
    """Return the LangSmith RunTree id for the current asyncio task, if any."""
    return _current_trace_id.get()


def get_current_span_id() -> str | None:
    """Return the current application span id for the current asyncio task, if any."""
    return _current_span_id.get()


def _safe_extra(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Build LangSmith ``extra`` dict, filtering out non-serializable values."""
    extra: dict[str, Any] = {}
    if not metadata:
        return extra
    for key, value in metadata.items():
        try:
            # Light serialization check; LangSmith accepts JSON-like values.
            if isinstance(value, (str, int, float, bool, list, dict)) or value is None:
                extra[key] = value
            else:
                extra[key] = str(value)
        except Exception:  # pragma: no cover - defensive
            continue
    return extra


class LangSmithTelemetryAdapter(TelemetryPort):
    """Emit spans to LangSmith using the SDK ``RunTree`` API.

    All LangSmith calls are wrapped in try/except so that telemetry failures
    never break the RAG pipeline.
    """

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://api.smith.langchain.com",
        project_name: str = "llm-wiki-rag",
    ):
        try:
            from langsmith import Client
            from langsmith.run_trees import RunTree
        except ImportError as exc:  # pragma: no cover - env guard
            raise ImportError("langsmith is required for LangSmithTelemetryAdapter") from exc

        self._Client = Client
        self._RunTree = RunTree
        self._client = Client(api_key=api_key, api_url=api_url)
        self._project_name = project_name
        self._runs: dict[str, Any] = {}

    async def start_span(
        self,
        name: str,
        kind: str,
        inputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        parent: TelemetrySpan | None = None,
    ) -> TelemetrySpan:
        span_id = str(uuid4())
        try:
            parent_run = self._runs.get(parent.span_id) if parent else None
            if parent_run is not None:
                # create_child links into the parent's trace — produces a
                # proper parent-child tree in LangSmith instead of isolated
                # root runs.
                run = parent_run.create_child(
                    name=name,
                    run_type=kind,
                    inputs=inputs or {},
                    extra={"metadata": _safe_extra(metadata)},
                )
            else:
                run = self._RunTree(
                    name=name,
                    run_type=kind,
                    inputs=inputs or {},
                    project_name=self._project_name,
                    extra={"metadata": _safe_extra(metadata)},
                    client=self._client,
                )
            run.post()
            self._runs[span_id] = run
            _current_trace_id.set(str(run.id))
            _current_span_id.set(span_id)
        except Exception as exc:
            logger.debug("LangSmith start_span failed: %s", exc, exc_info=True)
            return TelemetrySpan(span_id=span_id, name=name, kind=kind, metadata=metadata or {})

        return TelemetrySpan(span_id=span_id, name=name, kind=kind, metadata=metadata or {})

    async def end_span(
        self,
        span: TelemetrySpan,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        run = self._runs.pop(span.span_id, None)
        if run is None:
            return
        try:
            # Merge extra metadata BEFORE finalising so it reaches LangSmith.
            if metadata:
                run.extra = run.extra or {}
                existing = run.extra.get("metadata", {})
                existing.update(_safe_extra(metadata))
                run.extra["metadata"] = existing
            run.end(outputs=outputs or {}, error=error)
            run.patch()
        except Exception as exc:
            logger.debug("LangSmith end_span failed: %s", exc, exc_info=True)

    async def add_metadata(self, span: TelemetrySpan, metadata: dict[str, Any]) -> None:
        run = self._runs.get(span.span_id)
        if run is None:
            return
        try:
            run.extra = run.extra or {}
            existing = run.extra.get("metadata", {})
            existing.update(_safe_extra(metadata))
            run.extra["metadata"] = existing
        except Exception as exc:
            logger.debug("LangSmith add_metadata failed: %s", exc, exc_info=True)
