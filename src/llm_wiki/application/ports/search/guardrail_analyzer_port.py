"""Port for guardrail + intent analysis with per-tool search input generation.

A single lightweight LLM call replaces the old separate rewrite → analyze flow.
The prompt explains the entire RAG system so the model produces targeted inputs
for each search tool instead of generic keywords used for everything.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from llm_wiki.domain.value_objects.time_range import TimeRange


@dataclass
class GuardrailAnalysis:
    """Result of guardrail + intent analysis + search-input generation.

    Attributes:
        allowed: ``False`` when the question is outside the finance/economics domain.
        reason: Human-readable rejection reason when ``allowed`` is ``False``.
        intent: One of ``current_state``, ``historical``, ``timeline``,
            ``comparative``, ``factual_listing``, ``general``.
        language: ``"vi"`` or ``"en"`` — detected question language.
        time_range: Extracted date window or ``None``.
        entities: List of ``{"name": str, "type": str | None}`` dicts
            (e.g. ``{"name": "VCB", "type": "stock_ticker"}``).
        embedding_text: Optimised text (100-200 chars, bilingual VI+EN) for the
            single embedding used by both ``vector_search`` and ``event_search``.
            Should combine the question with key terms to maximise recall.
        page_search_query: OR-delimited keywords for full-text search on
            ``page_sections`` (long-form analysis, reports).  Focus on domain
            terminology, analytical concepts.
        event_search_query: OR-delimited keywords for full-text search on
            ``event_observations`` (timestamped events, observations).  Focus on
            proper nouns, concrete events, numbers.
        sub_questions: 1-4 sub-questions when the question is complex/multi-hop.
            Empty when answerable with a single retrieval pass.
    """

    allowed: bool = True
    reason: str = ""
    intent: str = "general"
    language: str = "vi"
    time_range: TimeRange | None = None
    entities: list[dict] = field(default_factory=list)
    embedding_text: str = ""
    page_search_query: str = ""
    event_search_query: str = ""
    sub_questions: list[str] = field(default_factory=list)


class GuardrailAnalyzerPort(ABC):
    """Analyse a question: guardrail check + intent + per-tool search inputs.

    Implementations MUST fall back to
    ``GuardrailAnalysis(allowed=True, intent="general")`` on any failure —
    analysis is a quality improvement, not a hard dependency.
    """

    @abstractmethod
    async def analyze(self, question: str) -> GuardrailAnalysis:
        """Analyse *question* and return a structured ``GuardrailAnalysis``."""
        ...
