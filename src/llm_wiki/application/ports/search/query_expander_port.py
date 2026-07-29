"""Port for expanding user queries with synonyms and alternative phrasings.

Improves keyword recall by generating Vietnamese synonyms and related terms
before the keyword search step. Especially important for domains like real
estate where "bất động sản" / "nhà đất" / "địa ốc" all refer to the same concept.
"""

from abc import ABC, abstractmethod


class QueryExpanderPort(ABC):
    """Expand a query into a richer keyword search string.

    Implementations should add synonyms, related terms, and alternative
    phrasings without changing the core intent of the query.
    """

    @abstractmethod
    async def expand(self, question: str, intent: str = "general") -> str:
        """Return an expanded version of *question* for keyword search.

        Args:
            question: The original or rewritten user question.
            intent: Query intent from ``QueryAnalyzerPort`` analysis.

        Returns:
            An expanded query string with additional keywords, or *question*
            unchanged on any failure.
        """
        ...
