"""LLM-based query analysis adapter — intent + time_range + entities + keywords + sub_questions."""

import json
import logging
from datetime import datetime

from llm_wiki.application.ports.search.query_analyzer_port import (
    QueryAnalysis,
    QueryAnalyzerPort,
)
from llm_wiki.application.ports.search.vector_search import LLMClientPort
from llm_wiki.domain.value_objects.time_range import TimeRange
from llm_wiki.shared.datetime_utils import now

logger = logging.getLogger(__name__)

ANALYZE_SYSTEM_PROMPT = """Phân tích câu hỏi người dùng. Output JSON (không markdown).

1. INTENT (phân loại):
- current_state: hỏi về tình hình hiện tại ("hiện nay", "bây giờ", "đang")
- historical: hỏi về quá khứ cụ thể ("năm 2023", "tháng trước")
- timeline: hỏi về diễn biến theo thời gian ("từ...đến nay", "diễn biến")
- comparative: hỏi so sánh ("so với", "khác gì", "giữa...và")
- general: câu hỏi chung

2. TIME_RANGE: trích xuất khoảng thời gian {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} hoặc null

3. ENTITIES: thực thể được nhắc đến
Loại: stock_ticker, commodity, location, macro_indicator, person, organization, policy
Mỗi entity: {"name": "...", "type": "..."}

4. KEYWORDS (QUAN TRỌNG — dùng cho full-text search):
- keywords: 3-8 từ khóa QUAN TRỌNG NHẤT, đã loại bỏ stop-words (cho, tôi, biết, những, nào, về, trong, là, có, các, và, của, được, không, để, với, sẽ, ra, này, đã, đang, từ...).
  Chỉ giữ: danh từ riêng, thuật ngữ chuyên ngành, số liệu, địa danh, tên tổ chức.
  Nếu câu hỏi tiếng Việt, THÊM cả bản tiếng Anh của thuật ngữ để search được cả nội dung tiếng Anh.
  Ví dụ: hỏi "vàng" → keywords: ["vàng", "gold", "giá vàng"]
  Ví dụ: hỏi "AI trong y tế" → keywords: ["AI", "artificial intelligence", "y tế", "healthcare", "medical"]
- key_phrases: 1-3 CỤM TỪ GHÉP quan trọng cần match chính xác.
  Ví dụ: ["thị trường chứng khoán", "lãi suất ngân hàng"]
- search_query: chuỗi dùng cho full-text search PostgreSQL tsquery.
  Các từ/cụm nối bằng " | " (OR logic). Bao gồm cả tiếng Việt VÀ tiếng Anh.
  Ví dụ: "vàng | gold | giá vàng | kim loại quý"

5. SUB_QUESTIONS: nếu câu hỏi PHỨC TẠP (nhiều khía cạnh, cần tra cứu nhiều bước), phân rã thành 2-4 câu hỏi con ĐƠN GIẢN, ĐỘC LẬP.
Nếu câu hỏi đơn giản → mảng rỗng [].

6. LANGUAGE: "vi" nếu câu hỏi bằng tiếng Việt, "en" nếu bằng tiếng Anh.

Output JSON đầy đủ:
{"intent": "...", "time_range": {"start": "YYYY-MM-DD hoặc null", "end": "YYYY-MM-DD hoặc null"}, "entities": [{"name": "...", "type": "..."}], "language": "vi", "keywords": [...], "key_phrases": [...], "search_query": "...", "sub_questions": [...]}

CHỈ output JSON, không markdown."""

# Intent → retrieval weights (from 29_LLM_wiki production config)
INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "current_state": {"events": 1.0, "sections": 0.7},
    "historical": {"events": 0.8, "sections": 0.5},
    "timeline": {"events": 1.0, "sections": 0.3},
    "comparative": {"events": 0.5, "sections": 0.8},
    "general": {"events": 0.4, "sections": 1.0},
}


def _extract_json(text: str) -> dict:
    """Extract a JSON object from *text*, even when wrapped in reasoning/thinking text.

    Reasoning models (deepseek-v4 with thinking enabled) return a
    ``reasoning_content`` field that prepends chain-of-thought narration
    before the actual JSON output.  This function finds the first balanced
    ``{``...``}`` block and parses it.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the outermost balanced JSON object
    # JSON5 / trailing commas are not supported, so keep it simple.
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    # The balance algorithm found a pair but it may include
                    # nested strings with unmatched braces; try to resync.
                    raise ValueError("Unbalanced JSON braces in response")
    raise ValueError("No balanced JSON object found in response")


class LLMQueryAnalyzerAdapter(QueryAnalyzerPort):
    """Analyse user question via a single lightweight LLM call.

    Falls back to ``QueryAnalysis(intent="general")`` on any failure —
    analysis is a quality improvement, not a hard dependency.
    """

    def __init__(
        self,
        llm: LLMClientPort,
        temperature: float = 0.0,
        max_tokens: int = 500,
    ):
        self._llm = llm
        self._temperature = temperature
        self._max_tokens = max_tokens

    @staticmethod
    def _parse_time_range(tr_data: dict | None) -> TimeRange | None:
        """Convert LLM JSON time_range into a TimeRange value object."""
        if not tr_data:
            return None
        start_str = tr_data.get("start")
        end_str = tr_data.get("end")
        if not start_str:
            return None
        try:
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str) if end_str else now()
            return TimeRange(start=start, end=end)
        except (ValueError, TypeError):
            return None

    async def analyze(self, question: str) -> QueryAnalysis:
        try:
            messages = [
                {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Câu hỏi: {question}"},
            ]
            raw = await self._llm.chat_completion(
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                enable_thinking=False,
            )
            parsed = _extract_json(raw)

            intent = parsed.get("intent", "general")
            time_range = self._parse_time_range(parsed.get("time_range"))
            language = parsed.get("language", "vi")

            raw_entities = parsed.get("entities", [])
            entities: list[dict] = []
            for ent in raw_entities:
                if isinstance(ent, str):
                    entities.append({"name": ent, "type": None})
                elif isinstance(ent, dict):
                    entities.append({"name": ent.get("name", ""), "type": ent.get("type")})

            # Keyword extraction for full-text search
            keywords: list[str] = [
                str(k).strip() for k in parsed.get("keywords", []) if k and str(k).strip()
            ]
            key_phrases: list[str] = [
                str(p).strip() for p in parsed.get("key_phrases", []) if p and str(p).strip()
            ]
            search_query = str(parsed.get("search_query", "") or "").strip()

            # Sub-questions for complex queries
            sub_questions: list[str] = [
                str(sq).strip() for sq in parsed.get("sub_questions", []) if sq and str(sq).strip()
            ]

            logger.debug(
                "Query analyzed: intent=%s lang=%s time_range=%s entities=%d keywords=%d phrases=%d sub_qs=%d",
                intent,
                language,
                time_range,
                len(entities),
                len(keywords),
                len(key_phrases),
                len(sub_questions),
            )
            return QueryAnalysis(
                intent=intent,
                time_range=time_range,
                entities=entities,
                language=language,
                keywords=keywords,
                key_phrases=key_phrases,
                search_query=search_query,
                sub_questions=sub_questions,
            )

        except Exception as exc:
            logger.warning("Query analysis failed, defaulting to general: %s", exc)
            return QueryAnalysis(intent="general", time_range=None, entities=[])
