"""Port for traversing the knowledge graph during RAG retrieval.

Given extracted entities from the query, traverse entity→event links
and event→event causal chains to enrich retrieval with structured
knowledge graph context.
"""

from abc import ABC, abstractmethod

from llm_wiki.domain.value_objects.embedding import SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange


class GraphRAGPort(ABC):
    """Traverse the knowledge graph to find additional relevant content."""

    @abstractmethod
    async def traverse(
        self,
        entities: list[dict],
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        """Given extracted *entities*, traverse the KG to find related events.

        Args:
            entities: List of ``{"name": str, "type": str | None}`` dicts.
            top_k: Max number of graph results to return.
            time_range: Optional time window filter.

        Returns:
            ``SearchResult`` list from graph traversal, ready to merge
            with other retrieval streams.
        """
        ...

    @abstractmethod
    async def traverse_timeline(
        self,
        event_ids: list[str],
        max_hop: int = 2,
    ) -> list[SearchResult]:
        """From known event IDs, traverse causal/temporal chains.

        Args:
            event_ids: UUIDs of known relevant events.
            max_hop: Max number of hops along the timeline chain.

        Returns:
            ``SearchResult`` list for causally related events.
        """
        ...
