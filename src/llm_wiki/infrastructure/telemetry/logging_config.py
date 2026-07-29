"""Centralized structured logging setup.

Provides JSON and plain-text formatters, a trace-id injection filter,
and a single ``setup_logging()`` entry point called by every service.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Emit log records as JSON lines with a consistent schema."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", "unknown"),
            "worker_id": getattr(record, "worker_id", None),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        return json.dumps(log_entry, default=str)


class TraceIdFilter(logging.Filter):
    """Injects the current LangSmith trace_id into every log record.

    Uses contextvars behind the scenes — safe across asyncio tasks.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from llm_wiki.infrastructure.telemetry.langsmith_telemetry_adapter import (
                get_current_trace_id,
            )
            trace_id = get_current_trace_id()
            if trace_id:
                record.trace_id = trace_id
        except Exception:
            pass
        return True


def setup_logging(service_name: str = "backend", log_format: str = "text") -> None:
    """Configure the root logger for the service.

    Call once at startup in every entrypoint (backend, cpu-worker, wiki-consumer).

    Args:
        service_name: Value written into the ``service`` field of every log line.
        log_format: ``"json"`` for structured output, ``"text"`` for human-readable.
    """
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)

    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )

    root.addHandler(handler)
    root.addFilter(TraceIdFilter())
    root.setLevel(logging.INFO)

    # Quiet noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
