from abc import ABC, abstractmethod
from typing import Optional

from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult


class VectorSearchPort(ABC):
    @abstractmethod
    async def search_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: Optional[str] = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def search_sections_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: Optional[str] = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def search_events_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
    ) -> list[SearchResult]: ...


class KeywordSearchPort(ABC):
    @abstractmethod
    async def search_keyword(
        self,
        query: str,
        top_k: int = 10,
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


class EmbeddingServicePort(ABC):
    @abstractmethod
    async def embed(self, text: str) -> Embedding: ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[Embedding]: ...


class CacheServicePort(ABC):
    @abstractmethod
    async def get(self, key: str) -> Optional[str]: ...

    @abstractmethod
    async def set(self, key: str, value: str, ttl: int = 3600) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...
