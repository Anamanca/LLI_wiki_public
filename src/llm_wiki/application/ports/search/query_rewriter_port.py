"""Port for rewriting follow-up questions into standalone queries using chat history."""

from abc import ABC, abstractmethod


class QueryRewriterPort(ABC):
    """Rewrite multi-turn follow-up questions into standalone queries.

    Resolves pronouns, implicit references, and context-dependent
    fragments from the last N chat turns so that downstream embedding
    and search receive a fully-qualified question.
    """

    @abstractmethod
    async def rewrite(self, question: str, history: list[dict]) -> str:
        """Return a standalone question, or *question* unchanged on failure.

        Args:
            question: The raw user question (may contain pronouns/implicit refs).
            history: Chat history as ``[{"role": "user"|"assistant", "content": ...}]``.

        Returns:
            Rewritten standalone question, or the original *question* on any error.
        """
        ...
