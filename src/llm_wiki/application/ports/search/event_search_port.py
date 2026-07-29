"""Port for searching event observations via vector similarity and keyword.

Events are stored in ``event_observations`` with their own embedding and
full-text search vectors, joined through ``event_canonicals`` for consensus
summaries and normalized dates. This is a separate retrieval target from
page sections — events capture discrete occurrences, sections capture
encyclopedic knowledge.
"""

from abc import ABC, abstractmethod

from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange


class EventSearchPort(ABC):
    """Search event observations (vector + keyword) with recency decay."""

    @abstractmethod
    async def search_events(
        self,
        embedding: Embedding,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        """Vector similarity search over ``event_observations.embedding``.

        Results are joined with ``event_canonicals`` for consensus summaries
        and ``pages``/``sources`` for metadata.  Recency decay is applied
        via ``EXP(-0.01 × days_old)``.
        """
        ...

    @abstractmethod
    async def search_events_keyword(
        self,
        query: str,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        """Full-text search over ``event_observations.fts_vector``.

        Uses PostgreSQL ``plainto_tsquery('simple', :query)`` with
        ``ts_rank`` scoring.
        """
        ...
