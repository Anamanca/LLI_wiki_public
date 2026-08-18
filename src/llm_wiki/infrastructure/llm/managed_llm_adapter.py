"""Managed LLM adapter — routes calls through the multi-key :class:`ApiKeyManager`.

Sits behind ``LLMClientPort`` (application layer) and delegates each call to a
fresh :class:`OpenAIAdapter` built from the key selected by the manager's
round-robin. This is what actually *uses* the api_keys table; without it the
manager is dead code and every request would use the single env key.

Error handling:
  - 401/403 (invalid/expired key) → mark key ``disabled`` + raise a
    system-level alert for the Web UI, then re-raise ``ApiKeyInvalidError``.
  - 429 (rate limited) → mark key ``rate_limited`` so the next round-robin
    picks another active key, then re-raise for the caller's own retry logic.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from llm_wiki.application.ports.search.vector_search import LLMClientPort
from llm_wiki.infrastructure.llm.api_key_manager import get_key_manager
from llm_wiki.infrastructure.llm.openai_adapter import OpenAIAdapter


class ApiKeyInvalidError(Exception):
    """Raised when the LLM provider rejects a key as invalid/expired (401/403)."""


class ManagedLLMAdapter(LLMClientPort):
    """Multi-key LLM adapter backed by :class:`ApiKeyManager`."""

    def __init__(self) -> None:
        self.last_usage: dict[str, Any] | None = None

    def _build_inner(self, config: dict[str, str]) -> OpenAIAdapter:
        return OpenAIAdapter(
            api_key=config["api_key"],
            base_url=config["base_url"],
            model=config.get("primary_model") or "",
            key_id=config.get("_key_id"),
        )

    async def _handle_status_error(self, exc: httpx.HTTPStatusError, key_id: str | None) -> None:
        status = exc.response.status_code if exc.response is not None else 0
        detail = str(exc)[:300]

        if status in (401, 403):
            await get_key_manager().mark_key_invalid(key_id, detail)
            raise ApiKeyInvalidError(f"LLM API key rejected ({status}): {detail}") from exc

        if status == 429 and key_id:
            # Default 30s cooldown; the auto-recovery loop also reactivates keys.
            await get_key_manager().mark_rate_limited(key_id, time.time() + 30)

        raise exc

    async def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        enable_thinking: bool = True,
    ) -> str:
        config = await get_key_manager().get_next_key()
        inner = self._build_inner(config)
        try:
            result = await inner.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            )
        except httpx.HTTPStatusError as exc:
            await self._handle_status_error(exc, config.get("_key_id"))
            raise  # unreachable — _handle_status_error always raises
        self.last_usage = inner.last_usage
        return result

    async def chat_completion_raw(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        enable_thinking: bool = True,
    ) -> dict:
        config = await get_key_manager().get_next_key()
        inner = self._build_inner(config)
        try:
            result = await inner.chat_completion_raw(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            )
        except httpx.HTTPStatusError as exc:
            await self._handle_status_error(exc, config.get("_key_id"))
            raise
        self.last_usage = inner.last_usage
        return result

    async def chat_completion_reasoning(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict[str, str]:
        config = await get_key_manager().get_next_key()
        inner = self._build_inner(config)
        try:
            result = await inner.chat_completion_reasoning(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except httpx.HTTPStatusError as exc:
            await self._handle_status_error(exc, config.get("_key_id"))
            raise
        self.last_usage = inner.last_usage
        return result

    async def chat_completion_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        config = await get_key_manager().get_next_key()
        inner = self._build_inner(config)
        try:
            async for chunk in inner.chat_completion_stream(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk
        except httpx.HTTPStatusError as exc:
            await self._handle_status_error(exc, config.get("_key_id"))
            raise
        finally:
            self.last_usage = inner.last_usage
