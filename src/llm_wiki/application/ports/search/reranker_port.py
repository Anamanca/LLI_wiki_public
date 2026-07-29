"""Port for cross-encoder re-ranking of retrieved documents.

Re-ranking improves precision by applying a deeper semantic comparison
between the query and each retrieved document. RRF fusion gives a fast
first-pass ranking; the re-ranker refines the top N results.
"""

from abc import ABC, abstractmethod

from llm_wiki.domain.value_objects.embedding import SearchResult


class RerankerPort(ABC):
    """Re-rank a list of retrieved documents by their relevance to a query."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: list[SearchResult],
        top_n: int = 20,
    ) -> list[SearchResult]:
        """Return *documents* re-sorted by relevance to *query*, truncated to *top_n*.

        Args:
            query: The user question.
            documents: Pre-ranked documents from RRF fusion.
            top_n: Number of top documents to keep after re-ranking.

        Returns:
            Re-ranked and truncated list of ``SearchResult``.
        """
        ...
