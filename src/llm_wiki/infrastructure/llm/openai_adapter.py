import json
import logging
from typing import Any, AsyncGenerator

import httpx

from llm_wiki.application.ports.search.vector_search import LLMClientPort
from llm_wiki.config import settings

logger = logging.getLogger()
llm_logger = logging.getLogger(__name__)


class OpenAIAdapter(LLMClientPort):
    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ):
        self._api_key = api_key or settings.opencode_api_key
        self._base_url = (base_url or settings.opencode_base_url).rstrip("/")
        self._model = model or settings.opencode_primary_model
        self.last_usage: dict[str, Any] | None = None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _is_reasoning_model(self) -> bool:
        """Return True when the configured model supports reasoning/thinking."""
        return self._model.startswith("deepseek-v4")

    def _thinking_payload(self, enable_thinking: bool = True) -> dict:
        """Return thinking parameter based on model, feature flag, and caller preference.

        Callers that don't benefit from chain-of-thought (e.g. JSON extraction,
        classification) can pass ``enable_thinking=False`` to save tokens and
        get a clean ``content`` field in the response.
        """
        if enable_thinking and self._is_reasoning_model() and settings.reasoning_enabled:
            return {"type": "enabled"}
        return {"type": "disabled"}

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> dict[str, Any] | None:
        """Extract usage from a chat completion response."""
        usage = data.get("usage")
        if not usage:
            return None
        return {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }

    async def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        enable_thinking: bool = True,
    ) -> str:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": self._thinking_payload(enable_thinking),
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
            self.last_usage = self._extract_usage(data)
            message = (data.get("choices") or [{}])[0].get("message") or {}
            content = message.get("content", "")
            # Fallback for reasoning models that return reasoning_content
            # when content is empty or missing.
            if not content:
                content = message.get("reasoning_content", "")
                if content:
                    logger.warning("Using reasoning_content as fallback for empty content")
            return content

    async def chat_completion_raw(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        enable_thinking: bool = True,
    ) -> dict:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking": self._thinking_payload(enable_thinking),
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, headers=self._headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
            self.last_usage = self._extract_usage(data)
            return data

    async def chat_completion_reasoning(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict[str, str]:
        """Return both final content and reasoning content for reasoning models."""
        data = await self.chat_completion_raw(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        message = (data.get("choices") or [{}])[0].get("message") or {}
        content = message.get("content", "") or ""
        reasoning_content = message.get("reasoning_content", "") or ""
        if not content and reasoning_content:
            logger.warning("Using reasoning_content as fallback for empty content")
            content = reasoning_content
        return {"content": content, "reasoning_content": reasoning_content}

    async def chat_completion_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            # deepseek-v4 models are reasoning models that burn token
            # budget on internal thinking. Disabling it ensures the stream
            # yields content chunks instead of keeping them all hidden inside
            # reasoning_content -> null delta.content -> zero visible tokens.
            "thinking": {"type": "disabled"},
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                async with client.stream("POST", url, headers=self._headers(), json=payload) as resp:
                    logger.warning("LLM stream status: %s", resp.status_code)
                    resp.raise_for_status()
                    chunk_count = 0
                    content_count = 0
                    self.last_usage = None
                    async for line in resp.aiter_lines():
                        chunk_count += 1
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            if not data_str.strip():
                                continue
                            try:
                                chunk = json.loads(data_str)
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    content = delta.get("content")
                                    if content:
                                        content_count += 1
                                        yield content
                                        continue
                                    # deepseek-v4 reasoning models may emit the final
                                    # answer inside reasoning_content deltas even when
                                    # thinking is disabled.  Fall back so the stream
                                    # never produces zero visible tokens.
                                    reasoning = delta.get("reasoning_content")
                                    if reasoning:
                                        content_count += 1
                                        yield reasoning
                                    continue
                                # Usage chunk appears when choices is empty and usage is present.
                                usage = chunk.get("usage")
                                if usage:
                                    self.last_usage = {
                                        "prompt_tokens": usage.get("prompt_tokens"),
                                        "completion_tokens": usage.get("completion_tokens"),
                                        "total_tokens": usage.get("total_tokens"),
                                    }
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                    logger.warning("LLM stream finished: chunks=%d, content_chunks=%d", chunk_count, content_count)
            except Exception as e:
                llm_logger.error("Stream error: %s", str(e))
                raise
