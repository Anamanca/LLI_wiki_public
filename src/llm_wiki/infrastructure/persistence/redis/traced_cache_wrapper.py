"""Tracing wrapper for CacheServicePort implementations."""

from __future__ import annotations

import time

from llm_wiki.application.ports.search.vector_search import CacheServicePort
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan


class TracedCacheWrapper(CacheServicePort):
    """Wraps a cache adapter with telemetry spans for hits and misses."""

    def __init__(
        self,
        inner: CacheServicePort,
        telemetry: TelemetryPort,
        parent_span: TelemetrySpan | None = None,
    ):
        self._inner = inner
        self._telemetry = telemetry
        self._parent_span = parent_span

    async def get(self, key: str) -> str | None:
        span = await self._telemetry.start_span(
            name="cache_get",
            kind="tool",
            inputs={"key_hash": _hash_key(key)},
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            value = await self._inner.get(key)
            latency_ms = (time.time() - t0) * 1000
            cache_hit = value is not None
            await self._telemetry.end_span(
                span=span,
                outputs={"cache_hit": cache_hit},
            )
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "cache_hit": cache_hit,
                },
            )
            return value
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "error_type": type(exc).__name__,
                },
            )
            await self._telemetry.end_span(span=span, error=str(exc))
            raise

    async def set(self, key: str, value: str, ttl: int = 3600) -> None:
        span = await self._telemetry.start_span(
            name="cache_set",
            kind="tool",
            inputs={
                "key_hash": _hash_key(key),
                "value_length": len(value),
                "ttl": ttl,
            },
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            await self._inner.set(key, value, ttl)
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.end_span(span=span, outputs={"stored": True})
            await self._telemetry.add_metadata(
                span=span,
                metadata={"latency_ms": round(latency_ms, 2)},
            )
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "error_type": type(exc).__name__,
                },
            )
            await self._telemetry.end_span(span=span, error=str(exc))
            raise

    async def delete(self, key: str) -> None:
        span = await self._telemetry.start_span(
            name="cache_delete",
            kind="tool",
            inputs={"key_hash": _hash_key(key)},
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            await self._inner.delete(key)
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.end_span(span=span, outputs={"deleted": True})
            await self._telemetry.add_metadata(
                span=span,
                metadata={"latency_ms": round(latency_ms, 2)},
            )
        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "error_type": type(exc).__name__,
                },
            )
            await self._telemetry.end_span(span=span, error=str(exc))
            raise


def _hash_key(key: str) -> str:
    """Return a short, deterministic hash of the cache key for traces."""
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()[:16]
