"""Tracing wrapper for LLMClientPort implementations."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any

from llm_wiki.application.ports.search.vector_search import LLMClientPort
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan


def _redacted_messages(messages: list[dict]) -> list[dict]:
    """Store message roles and rough lengths; avoid logging full content."""
    return [
        {"role": m.get("role"), "content_length": len(m.get("content", ""))}
        for m in messages
    ]


class TracedLLMWrapper(LLMClientPort):
    """Wraps an LLM client and emits telemetry spans for each call."""

    def __init__(
        self,
        inner: LLMClientPort,
        telemetry: TelemetryPort,
        model: str = "unknown",
        parent_span: TelemetrySpan | None = None,
    ):
        self._inner = inner
        self._telemetry = telemetry
        self._model = model
        self._parent_span = parent_span
        self.last_usage: dict[str, Any] | None = None

    def set_parent_span(self, parent: TelemetrySpan) -> None:
        """Wire this wrapper's spans under a parent span (e.g. pipeline root)."""
        self._parent_span = parent

    async def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        span = await self._telemetry.start_span(
            name="llm_chat_completion",
            kind="llm",
            inputs={
                "model": self._model,
                "messages": _redacted_messages(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            metadata={"model": self._model},
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            answer = await self._inner.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency_ms = (time.time() - t0) * 1000
            usage = getattr(self._inner, "last_usage", None)
            self.last_usage = usage
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "answer_length": len(answer),
                    "answer_preview": answer[:200],
                },
            )
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "tokens_used": usage.get("total_tokens") if usage else None,
                    "prompt_tokens": usage.get("prompt_tokens") if usage else None,
                    "completion_tokens": usage.get("completion_tokens") if usage else None,
                },
            )
            return answer
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

    async def chat_completion_raw(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        span = await self._telemetry.start_span(
            name="llm_chat_completion_raw",
            kind="llm",
            inputs={
                "model": self._model,
                "messages": _redacted_messages(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            metadata={"model": self._model},
            parent=self._parent_span,
        )
        t0 = time.time()
        try:
            data = await self._inner.chat_completion_raw(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            latency_ms = (time.time() - t0) * 1000
            usage = data.get("usage") if isinstance(data, dict) else None
            self.last_usage = usage
            content = ""
            if isinstance(data, dict):
                choices = data.get("choices", [{}])
                if choices:
                    content = choices[0].get("message", {}).get("content", "") or ""
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "answer_length": len(content),
                    "answer_preview": content[:200],
                },
            )
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "tokens_used": usage.get("total_tokens") if usage else None,
                    "prompt_tokens": usage.get("prompt_tokens") if usage else None,
                    "completion_tokens": usage.get("completion_tokens") if usage else None,
                },
            )
            return data
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

    async def chat_completion_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        span = await self._telemetry.start_span(
            name="llm_chat_completion_stream",
            kind="llm",
            inputs={
                "model": self._model,
                "messages": _redacted_messages(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            metadata={"model": self._model},
            parent=self._parent_span,
        )
        t0 = time.time()
        full_answer = ""
        try:
            async for chunk in self._inner.chat_completion_stream(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                full_answer += chunk
                yield chunk
            latency_ms = (time.time() - t0) * 1000
            usage = getattr(self._inner, "last_usage", None)
            self.last_usage = usage
            await self._telemetry.end_span(
                span=span,
                outputs={
                    "answer_length": len(full_answer),
                    "answer_preview": full_answer[:200],
                },
            )
            await self._telemetry.add_metadata(
                span=span,
                metadata={
                    "latency_ms": round(latency_ms, 2),
                    "tokens_used": usage.get("total_tokens") if usage else None,
                    "prompt_tokens": usage.get("prompt_tokens") if usage else None,
                    "completion_tokens": usage.get("completion_tokens") if usage else None,
                },
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
