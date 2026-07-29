"""Port for evaluating RAG answer quality online.

Provides LLM-as-judge scoring for faithfulness, completeness, and relevance
so the pipeline can decide whether to stop or retry.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AnswerEvaluation:
    """Result of evaluating a generated answer.

    Attributes:
        faithfulness: 0-10, how well the answer is grounded in context.
        completeness: 0-10, how completely the answer addresses the question.
        relevance: 0-10, how relevant the retrieved context is to the question.
        should_stop: True when the answer is good enough to return.
        missing_info: What information is still missing (if any).
        refined_query: Suggested query rewrite for retry (if not stopping).
        suggested_strategy: Next strategy: refine_query | decompose | hyde | expand.
    """

    faithfulness: float = 0.0
    completeness: float = 0.0
    relevance: float = 0.0
    should_stop: bool = True
    missing_info: str = ""
    refined_query: str = ""
    suggested_strategy: str = "refine_query"


class AnswerEvaluatorPort(ABC):
    """Evaluate a generated RAG answer for quality using LLM-as-judge."""

    @abstractmethod
    async def evaluate(
        self,
        question: str,
        context: str,
        answer: str,
        intent: str = "general",
    ) -> AnswerEvaluation:
        """Evaluate answer quality and return structured feedback.

        Args:
            question: Original user question.
            context: Retrieved context fed to the LLM.
            answer: Generated answer to evaluate.
            intent: Query intent for context-aware evaluation.

        Returns:
            ``AnswerEvaluation`` with scores and retry guidance.
        """
        ...
