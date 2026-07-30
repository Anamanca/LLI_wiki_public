"""LLM-based query rewriting adapter — resolves pronouns from chat history."""

import logging

from llm_wiki.application.ports.search.query_rewriter_port import QueryRewriterPort
from llm_wiki.application.ports.search.vector_search import LLMClientPort

logger = logging.getLogger(__name__)

REWRITE_SYSTEM_PROMPT = (
    "Bạn là trợ lý viết lại câu hỏi. Nhiệm vụ:"
    " Tạo câu hỏi ĐỘC LẬP, ĐẦY ĐỦ ngữ cảnh từ lịch sử chat."
    "\n\nQUY TẮC:\n"
    '1. Giải quyết đại từ/ẩn dụ: "nó", "ông ấy", "cái đó",'
    ' "vụ này", "thế còn" → thay bằng danh từ cụ thể\n'
    "2. Giữ nguyên ý định người dùng, không thêm thông tin mới\n"
    "3. Nếu câu hỏi đã độc lập, trả về nguyên bản\n"
    "4. Output: CHỈ câu hỏi đã viết lại, không thêm gì khác\n"
    "\nVí dụ:\n"
    'Lịch sử: ["Giá dầu thô hiện nay thế nào?",'
    ' "Giá dầu thô WTI đang ở 72 USD/thùng"]\n'
    'Câu hỏi: "còn vàng thì sao?"\n'
    "→ Giá vàng hiện nay thế nào?"
)


class LLMQueryRewriterAdapter(QueryRewriterPort):
    """Rewrites follow-up questions via an LLM call.

    Uses chat history (last 6 turns) to resolve pronouns and produce
    a standalone question suitable for embedding.
    """

    def __init__(
        self,
        llm: LLMClientPort,
        max_history_turns: int = 6,
        temperature: float = 0.0,
        max_tokens: int = 200,
    ):
        self._llm = llm
        self._max_history_turns = max_history_turns
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def rewrite(self, question: str, history: list[dict]) -> str:
        if not history:
            return question

        recent = history[-(self._max_history_turns * 2) :]
        history_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in recent
        )

        messages = [
            {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": f"Lịch sử:\n{history_text}\n\nCâu hỏi: {question}"},
        ]

        try:
            rewritten = await self._llm.chat_completion(
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            rewritten = rewritten.strip()
            if rewritten:
                logger.debug("Query rewritten: %r → %r", question[:80], rewritten[:80])
                return rewritten
            return question
        except Exception:
            logger.warning("Query rewrite failed, using original question")
            return question
