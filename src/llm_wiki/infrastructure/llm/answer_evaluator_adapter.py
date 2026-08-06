"""LLM-based answer evaluation adapter — online quality scoring for RAG answers.

Evaluates each generated answer on faithfulness, completeness, and relevance
so the reflective pipeline can decide whether to stop or retry.
"""

import json
import logging

from llm_wiki.application.ports.search.answer_evaluator_port import (
    AnswerEvaluation,
    AnswerEvaluatorPort,
)
from llm_wiki.application.ports.search.vector_search import LLMClientPort

logger = logging.getLogger(__name__)

EVALUATE_SYSTEM_PROMPT = """Bạn là chuyên gia đánh giá chất lượng câu trả lời RAG.
Đánh giá câu trả lời dựa trên NGỮ CẢNH được cung cấp. Thang điểm 0-10.

FAITHFULNESS (độ trung thực — mọi claim phải có trong context):
  0-2: hoàn toàn bịa đặt | 3-4: phần lớn suy đoán | 5-6: nửa suy đoán
  7-8: hầu hết grounded, 1-2 chi tiết chưa chắc | 9-10: mọi claim đều có trong context

COMPLETENESS (độ đầy đủ — trả lời được bao nhiêu khía cạnh của câu hỏi):
  0-2: không trả lời được | 3-4: chỉ chạm nhẹ | 5-6: trả lời 1 phần
  7-8: trả lời chính, thiếu 1-2 khía cạnh phụ | 9-10: đầy đủ mọi khía cạnh

RELEVANCE (độ liên quan của context với câu hỏi):
  0-2: hoàn toàn lạc đề | 3-4: ít liên quan | 5-6: có vài điểm liên quan
  7-8: nhiều thông tin liên quan | 9-10: context chứa đầy đủ thông tin cần thiết

QUY TẮC DỪNG:
- should_stop=true khi faithfulness>=7 AND completeness>=7
- should_stop=true khi answer là "không tìm thấy"/empty context
- should_stop=false khi faithfulness<5 hoặc completeness<5
- suggested_strategy: "refine_query" (relevance<5) | "decompose" (completeness<5+câu hỏi phức tạp)
  | "hyde" (relevance 5-7 & faithfulness<7) | "expand" (relevance 5-7 & context hơi ít)

Output JSON: {"faithfulness":0-10,"completeness":0-10,"relevance":0-10,
"should_stop":true/false,"missing_info":"...","refined_query":"...","suggested_strategy":"..."}
CHỈ output JSON, không markdown."""


class LLMAnswerEvaluatorAdapter(AnswerEvaluatorPort):
    """Evaluate RAG answer quality via an LLM call.

    Falls back to ``should_stop=True`` on any failure — the pipeline
    returns the current answer rather than looping forever.
    """

    def __init__(
        self,
        llm: LLMClientPort,
        temperature: float = 0.0,
        max_tokens: int = 300,
    ):
        self._llm = llm
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def evaluate(
        self,
        question: str,
        context: str,
        answer: str,
        intent: str = "general",
    ) -> AnswerEvaluation:
        # Quick checks before LLM call
        if not answer or not answer.strip():
            return AnswerEvaluation(
                faithfulness=0.0,
                completeness=0.0,
                relevance=0.0,
                should_stop=True,
                missing_info="No answer generated",
                suggested_strategy="refine_query",
            )

        if not context or not context.strip():
            return AnswerEvaluation(
                faithfulness=0.0,
                completeness=0.0,
                relevance=0.0,
                should_stop=True,
                missing_info="No context retrieved",
            )

        try:
            # Truncate to keep the evaluation prompt compact
            context_preview = context[:3000]
            answer_preview = answer[:2000]

            user_prompt = (
                f"Câu hỏi: {question}\n\n"
                f"Ngữ cảnh (retrieved):\n{context_preview}\n\n"
                f"Câu trả lời:\n{answer_preview}"
            )

            messages = [
                {"role": "system", "content": EVALUATE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            raw = await self._llm.chat_completion(
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )

            parsed = json.loads(raw.strip())

            evaluation = AnswerEvaluation(
                faithfulness=float(parsed.get("faithfulness", 5.0)),
                completeness=float(parsed.get("completeness", 5.0)),
                relevance=float(parsed.get("relevance", 5.0)),
                should_stop=bool(parsed.get("should_stop", True)),
                missing_info=str(parsed.get("missing_info", "")),
                refined_query=str(parsed.get("refined_query", "")),
                suggested_strategy=str(parsed.get("suggested_strategy", "refine_query")),
            )

            logger.debug(
                "Eval: F=%.1f C=%.1f R=%.1f stop=%s strategy=%s",
                evaluation.faithfulness,
                evaluation.completeness,
                evaluation.relevance,
                evaluation.should_stop,
                evaluation.suggested_strategy,
            )

            return evaluation

        except Exception as exc:
            logger.warning("Answer evaluation failed: %s, defaulting to stop", exc)
            return AnswerEvaluation(
                faithfulness=5.0,
                completeness=5.0,
                relevance=5.0,
                should_stop=True,
                missing_info=f"Evaluation error: {exc}",
            )
