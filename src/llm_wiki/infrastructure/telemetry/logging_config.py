"""Centralized structured logging setup.

Provides JSON and plain-text formatters, trace-id and span-id injection,
worker-id injection, and a single ``setup_logging()`` entry point called by
every service.
"""

from __future__ import annotations

import json
import logging
import sys

from llm_wiki.shared.datetime_utils import now

# Internal LogRecord attributes — skip these when merging `extra` dict fields.
_LOG_RECORD_INTERNALS = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        # Our own injected fields are handled separately.
        "service",
        "worker_id",
        "trace_id",
        "span_id",
    }
)


class JsonFormatter(logging.Formatter):
    """Emit log records as JSON lines with a consistent schema.

    Any ``extra`` dict passed to ``logger.info("msg", extra={...})`` is
    merged into the JSON output automatically.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": now().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", "unknown"),
            "worker_id": getattr(record, "worker_id", None),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
            "message": record.getMessage(),
        }
        # Merge user-supplied ``extra`` fields (e.g. from request logging middleware).
        for key, value in record.__dict__.items():
            if key not in _LOG_RECORD_INTERNALS and not key.startswith("_"):
                log_entry[key] = value
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry, default=str)


class TraceIdFilter(logging.Filter):
    """Injects the current LangSmith trace_id and span_id into every log record.

    Uses contextvars behind the scenes — safe across asyncio tasks.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from llm_wiki.infrastructure.telemetry.langsmith_telemetry_adapter import (
                get_current_span_id,
                get_current_trace_id,
            )

            trace_id = get_current_trace_id()
            if trace_id:
                record.trace_id = trace_id
            span_id = get_current_span_id()
            if span_id:
                record.span_id = span_id
        except Exception:
            pass
        return True


class ServiceNameFilter(logging.Filter):
    """Injects the service name into every log record so ``JsonFormatter``
    never emits ``"service": "unknown"``."""

    def __init__(self, service_name: str):
        super().__init__()
        self._service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self._service_name
        return True


class WorkerIdFilter(logging.Filter):
    """Injects the pre-resolved worker / consumer id into every log record.

    The value is read once from ``settings`` at process startup so it does
    not depend on environment variable lookups on every log line.
    """

    def __init__(self, worker_id: int | str | None = None):
        super().__init__()
        self._worker_id = worker_id

    def filter(self, record: logging.LogRecord) -> bool:
        if self._worker_id is not None:
            record.worker_id = self._worker_id
        return True


def setup_logging(
    service_name: str = "backend",
    log_format: str = "text",
    log_level: str = "INFO",
    worker_id: int | str | None = None,
) -> None:
    """Configure the root logger for the service.

    Call once at startup in every entrypoint (backend, cpu-worker, wiki-consumer).

    Args:
        service_name: Value written into the ``service`` field of every log line.
        log_format: ``"json"`` for structured output, ``"text"`` for human-readable.
        log_level: Python log level name, e.g. ``"DEBUG"``, ``"INFO"``, ``"WARNING"``.
        worker_id: Optional numeric id injected into every log record.
    """
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)

    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root.addHandler(handler)
    root.addFilter(ServiceNameFilter(service_name))
    root.addFilter(TraceIdFilter())
    root.addFilter(WorkerIdFilter(worker_id=worker_id))
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Quiet noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
