"""LLM-based query expansion adapter — generates synonyms/alternative phrasings."""

import logging

from llm_wiki.application.ports.search.query_expander_port import QueryExpanderPort
from llm_wiki.application.ports.search.vector_search import LLMClientPort

logger = logging.getLogger(__name__)

EXPAND_SYSTEM_PROMPT = """Bạn là trợ lý mở rộng từ khóa tìm kiếm tiếng Việt.
Nhiệm vụ: Sinh 3-5 từ khóa đồng nghĩa / cách diễn đạt khác cho câu query.

QUY TẮC:
1. Giữ nguyên ý nghĩa cốt lõi của query
2. Ưu tiên từ đồng nghĩa phổ biến trong tiếng Việt
3. Bao gồm cả cách viết tắt nếu có (vd: "TP.HCM" → "Thành phố Hồ Chí Minh")
4. Output: MỖI TỪ MỘT DÒNG, không đánh số, không markdown
5. Chỉ trả về danh sách từ khóa, không thêm giải thích

Ví dụ:
Query: giá vàng hôm nay
→ giá vàng
→ tỷ giá vàng SJC
→ vàng miếng
→ vàng nhẫn

Query: bất động sản quận 1
→ bất động sản
→ nhà đất quận 1
→ căn hộ quận 1
→ địa ốc trung tâm"""


class LLMQueryExpanderAdapter(QueryExpanderPort):
    """Expand query via a single lightweight LLM call.

    Falls back to returning the original question on any failure.
    """

    def __init__(
        self,
        llm: LLMClientPort,
        temperature: float = 0.0,
        max_tokens: int = 100,
    ):
        self._llm = llm
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def expand(self, question: str, intent: str = "general") -> str:
        try:
            messages = [
                {"role": "system", "content": EXPAND_SYSTEM_PROMPT},
                {"role": "user", "content": f"Query: {question}"},
            ]
            raw = await self._llm.chat_completion(
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            terms = [line.strip() for line in raw.strip().split("\n") if line.strip()]
            # Remove any lines that look like markdown bullets or numbers
            terms = [
                t for t in terms
                if not t.startswith(("- ", "* ", "1.", "2.", "3.", "4.", "5.", "•"))
            ]
            if not terms:
                return question
            # Deduplicate and merge with original question
            seen = {question.lower()}
            unique_terms = []
            for t in terms:
                if t.lower() not in seen:
                    seen.add(t.lower())
                    unique_terms.append(t)
            expanded = f"{question} {' '.join(unique_terms)}"
            logger.debug("Query expanded: %r → %r", question, expanded)
            return expanded
        except Exception:
            logger.debug("Query expansion failed, using original question")
            return question
