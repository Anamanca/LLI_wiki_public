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
Đánh giá câu trả lời dựa trên NGỮ CẢNH được cung cấp.

Thang điểm 0-10 cho từng tiêu chí:

FAITHFULNESS (độ trung thực):
- 9-10: Mọi thông tin đều có trong context, không bịa đặt
- 7-8: Hầu hết có trong context, 1-2 chi tiết nhỏ không chắc chắn
- 5-6: Một nửa có trong context, nửa còn lại suy đoán
- 3-4: Phần lớn suy đoán, ít bằng chứng từ context
- 0-2: Hoàn toàn bịa đặt hoặc không dựa trên context

COMPLETENESS (độ đầy đủ):
- 9-10: Trả lời đầy đủ mọi khía cạnh của câu hỏi
- 7-8: Trả lời chính nhưng thiếu 1-2 khía cạnh phụ
- 5-6: Trả lời được 1 phần, còn thiếu nhiều
- 3-4: Chỉ chạm nhẹ vào câu hỏi, thiếu chiều sâu
- 0-2: Không trả lời được câu hỏi

RELEVANCE (độ liên quan của context):
- 9-10: Context chứa đầy đủ thông tin cần thiết
- 7-8: Context có nhiều thông tin liên quan
- 5-6: Context có 1 số điểm liên quan nhưng không đủ
- 3-4: Context ít liên quan, lạc đề
- 0-2: Context hoàn toàn không liên quan

QUY TẮC DỪNG:
- should_stop = true KHI faithfulness >= 7 AND completeness >= 7
- should_stop = true KHI context trống hoặc không có kết quả (trả lời "không tìm thấy")
- should_stop = false KHI faithfulness < 5 hoặc completeness < 5
- suggested_strategy:
  - "refine_query" khi relevance < 5 (context không liên quan → viết lại query)
  - "decompose" khi completeness < 5 và câu hỏi phức tạp
  - "hyde" khi relevance 5-7 và faithfulness < 7
  - "expand" khi relevance 5-7 nhưng context hơi ít

Output JSON:
{"faithfulness": 0-10, "completeness": 0-10, "relevance": 0-10,
 "should_stop": true/false, "missing_info": "...",
 "refined_query": "...", "suggested_strategy": "..."}

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
