from abc import ABC, abstractmethod

from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange


class VectorSearchPort(ABC):
    @abstractmethod
    async def search_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: str | None = None,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def search_sections_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: str | None = None,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def search_events_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]: ...


class KeywordSearchPort(ABC):
    @abstractmethod
    async def search_keyword(
        self,
        query: str,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]: ...


class LLMClientPort(ABC):
    @abstractmethod
    async def chat_completion(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str: ...

    @abstractmethod
    async def chat_completion_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ): ...

    async def chat_completion_raw(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict:
        """Return the raw chat completion response dict.

        Default implementation uses the string endpoint and wraps the result in
        an OpenAI-compatible shape. Adapters that already receive a raw dict can
        override this to avoid the round-trip.
        """
        content = await self.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            "usage": getattr(self, "last_usage", None),
        }

    async def chat_completion_reasoning(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> dict[str, str]:
        """Return content and optional reasoning content from the LLM.

        Default implementation calls chat_completion and returns empty
        reasoning_content. Adapters for reasoning models should override this
        to expose the model's internal reasoning.
        """
        content = await self.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return {"content": content, "reasoning_content": ""}


class EmbeddingServicePort(ABC):
    @abstractmethod
    async def embed(self, text: str) -> Embedding: ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[Embedding]: ...


class CacheServicePort(ABC):
    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str, ttl: int = 3600) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    async def semantic_get(self, embedding: list[float], threshold: float = 0.95) -> str | None:
        """Check semantic cache by embedding similarity.

        Returns the cached answer value if a match is found, or None.
        Default implementation returns None (semantic cache disabled).
        Override in adapters that support embedding storage.
        """
        return None

    async def semantic_set(
        self, key: str, embedding: list[float], value: str, ttl: int = 3600
    ) -> None:
        """Store an embedding alongside its cache value for future semantic matching.

        Default implementation is a no-op. Override in adapters that support
        embedding storage.
        """
        return None
