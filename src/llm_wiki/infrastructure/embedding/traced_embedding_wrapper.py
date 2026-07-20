"""Tracing wrapper for EmbeddingServicePort implementations."""

from __future__ import annotations

import time

from llm_wiki.application.ports.search.vector_search import EmbeddingServicePort
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan
from llm_wiki.domain.value_objects.embedding import Embedding


class TracedEmbeddingWrapper(EmbeddingServicePort):
    """Wraps an embedding adapter with telemetry spans."""

    def __init__(
        self,
        inner: EmbeddingServicePort,
        telemetry: TelemetryPort,
        model: str = "unknown",
        parent_span: TelemetrySpan | None = None,
    ):
        self._inner = inner
        self._telemetry = telemetry
        self._model = model
        self._parent_span = parent_span

    async def embed(self, text: str) -> Embedding:
        span = await self._telemetry.start_span(
            name="embedding",
            kind="embedding",
            inputs={
                "model": self._model,
                "text_length": len(text),
            },
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            embedding = await self._inner.embed(text)
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "dimensions": embedding.dimensions if embedding else None,
                },
            )
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "model": self._model,
                },
            )
            return embedding
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

    async def embed_batch(self, texts: list[str]) -> list[Embedding]:
        span = await self._telemetry.start_span(
            name="embedding_batch",
            kind="embedding",
            inputs={
                "model": self._model,
                "batch_size": len(texts),
                "total_text_length": sum(len(t) for t in texts),
            },
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            embeddings = await self._inner.embed_batch(texts)
            latency_ms = (time.time() - t0) * 1000
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "embedding_count": len(embeddings),
                    "dimensions": embeddings[0].dimensions if embeddings else None,
                },
            )
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "model": self._model,
                },
            )
            return embeddings
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
