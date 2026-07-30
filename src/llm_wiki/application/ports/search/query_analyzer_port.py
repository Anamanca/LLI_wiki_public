""" "Port for analysing user queries: intent, time range, entities, and keywords.

One lightweight LLM call produces JSON that drives:
- retrieval weights (intent → events vs sections emphasis)
- SQL time filters (time_range → WHERE clause)
- entity-filtered search + GraphRAG traversal (entities → JOINs)
- keyword extraction (keywords / key_phrases / search_query → full-text search)
- complex question decomposition (sub_questions → multi-hop retrieval)
- synthesis language + temporal addendum (language + intent → prompt instructions)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from llm_wiki.domain.value_objects.time_range import TimeRange


@dataclass
class QueryAnalysis:
    """Result of analysing a user question.

    Attributes:
        intent: One of ``current_state``, ``historical``, ``timeline``,
            ``comparative``, ``general``.
        time_range: Extracted date window or ``None``.
        entities: List of ``{"name": str, "type": str | None}`` dicts.
        language: ``"vi"`` or ``"en"`` — detected question language.
        keywords: 3-8 extracted keywords (stop-words removed, bilingual when
            relevant).  Used as input for ``plainto_tsquery`` keyword search.
        key_phrases: 1-3 important compound phrases (e.g. "thị trường chứng khoán").
        search_query: OR-delimited string for ``to_tsquery`` fallback search
            (e.g. "ai | sự kiện | tháng 7 | 2025 | artificial intelligence").
        sub_questions: 1-4 sub-questions for complex multi-hop questions.
            Empty when the question is answerable with a single retrieval.
    """

    intent: str = "general"
    time_range: TimeRange | None = None
    entities: list[dict] = field(default_factory=list)
    language: str = "vi"
    keywords: list[str] = field(default_factory=list)
    key_phrases: list[str] = field(default_factory=list)
    search_query: str = ""
    sub_questions: list[str] = field(default_factory=list)


class QueryAnalyzerPort(ABC):
    """Analyse a user question for intent, time range, and named entities."""

    @abstractmethod
    async def analyze(self, question: str) -> QueryAnalysis:
        """Analyse *question* and return a structured ``QueryAnalysis``.

        Implementations MUST fall back to ``QueryAnalysis(intent="general")``
        on any failure — analysis is a quality improvement, not a hard
        dependency.
        """
        ...
