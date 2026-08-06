"""LLM-based guardrail + intent analysis + per-tool search input adapter.

One call replaces the old query_rewrite → query_analyze chain.  The prompt
describes the full RAG system — what each search tool indexes, how results
are fused — so the model produces targeted inputs per tool rather than one
generic keyword blob for everything.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from llm_wiki.application.ports.search.guardrail_analyzer_port import (
    GuardrailAnalysis,
    GuardrailAnalyzerPort,
)
from llm_wiki.application.ports.search.vector_search import LLMClientPort
from llm_wiki.domain.value_objects.time_range import TimeRange
from llm_wiki.shared.datetime_utils import get_system_tz, now

logger = logging.getLogger(__name__)

# ── System prompt ──────────────────────────────────────────────────────────
# Three sections: (0) guardrail, (1) intent+time_range, (2) tool-aware search inputs

_GUARDRAIL_ANALYZE_PROMPT = (
    # ═══════════════════════════════════════════════════════════════════════
    # 0. GUARDRAIL — bạn là ai, domain nào bạn trả lời
    # ═══════════════════════════════════════════════════════════════════════
    "Bạn là trợ lý phân tích câu hỏi cho hệ thống RAG về LĨNH VỰC KINH TẾ - TÀI CHÍNH.\n\n"
    "DOMAIN ĐƯỢC PHÉP TRẢ LỜI:\n"
    "- Chứng khoán, cổ phiếu, trái phiếu, phái sinh, ETF, quỹ đầu tư\n"
    "- Ngân hàng, lãi suất, tín dụng, tỷ giá, ngoại hối\n"
    "- Kinh tế vĩ mô: GDP, lạm phát, CPI, PMI, xuất nhập khẩu, FDI\n"
    "- Hàng hóa: vàng, dầu, thép, cao su, cà phê, gạo,...\n"
    "- Bất động sản, thị trường nhà đất\n"
    "- Tiền điện tử, crypto, blockchain (khía cạnh tài chính)\n"
    "- Doanh nghiệp, báo cáo tài chính, M&A, cổ tức\n"
    "- Chính sách tài khóa, tiền tệ, thuế\n"
    "- Đầu tư cá nhân, quản lý danh mục, phân tích kỹ thuật/cơ bản\n\n"
    "DOMAIN TỪ CHỐI (trả lời allowed=false):\n"
    "- Giải trí, phim ảnh, âm nhạc, game, thể thao, du lịch\n"
    "- Sức khỏe, y tế, nấu ăn, ẩm thực, thời trang\n"
    "- Công nghệ thuần túy (code, phần mềm, gadget) trừ khi liên quan đến\n"
    "  cổ phiếu công nghệ hoặc đầu tư vào tech\n"
    "- Chính trị, tôn giáo, triết học, lịch sử không liên quan kinh tế\n"
    "- Toán học, khoa học tự nhiên, giáo dục không liên quan tài chính\n"
    "- Đời sống cá nhân, tâm lý, tình cảm, gia đình\n\n"
    # ═══════════════════════════════════════════════════════════════════════
    # 1. INTENT + TIME RANGE
    # ═══════════════════════════════════════════════════════════════════════
    "1. INTENT — xác định LOẠI CÂU HỎI để xác định KHOẢNG THỜI GIAN dữ liệu cần lấy:\n"
    "- current_state: hỏi tình hình HIỆN TẠI (\"hiện nay\", \"bây giờ\", \"đang\",\n"
    "  \"mới nhất\") → time_range lấy ~30 ngày gần đây\n"
    "- historical: hỏi về MỐC THỜI GIAN CỤ THỂ trong quá khứ (\"năm 2023\",\n"
    "  \"tháng 6/2024\", \"quý 1/2025\") → time_range trích xuất chính xác từ câu hỏi\n"
    "- timeline: hỏi về DIỄN BIẾN theo thời gian (\"từ 2020 đến nay\", \"diễn biến\",\n"
    "  \"xu hướng\", \"lịch sử giá\") → time_range từ mốc bắt đầu đến hiện tại\n"
    "- comparative: hỏi SO SÁNH (\"so với\", \"khác gì\", \"giữa...và...\") →\n"
    "  time_range trích xuất 1-2 khoảng thời gian từ câu hỏi\n"
    "- factual_listing: hỏi DANH SÁCH, LIỆT KÊ (\"liệt kê\", \"danh sách\",\n"
    "  \"các mã\", \"những cổ phiếu nào\", \"có những...nào\") → không giới hạn\n"
    "  thời gian (ưu tiên section search vì danh sách thường có trong bài viết)\n"
    "- general: câu hỏi chung, khái niệm, định nghĩa → không giới hạn thời gian\n\n"
    "2. TIME_RANGE: trích xuất chính xác khoảng thời gian.\n"
    '  Output: {"start": "YYYY-MM-DD hoặc null", "end": "YYYY-MM-DD hoặc null"}.\n'
    "  - current_state → start = ~30 ngày trước, end = hôm nay\n"
    "  - historical → start & end từ câu hỏi (nếu chỉ có năm: start=YYYY-01-01,\n"
    "    end=YYYY-12-31)\n"
    "  - timeline → start từ mốc bắt đầu, end = hôm nay\n"
    "  - factual_listing / general / comparative không có thời gian cụ thể → null\n\n"
    # ═══════════════════════════════════════════════════════════════════════
    # 2. TOOL-AWARE SEARCH INPUTS
    # ═══════════════════════════════════════════════════════════════════════
    "3. HỆ THỐNG CÓ 4 CÔNG CỤ TÌM KIẾM. Mỗi công cụ tìm trong một loại dữ liệu KHÁC NHAU.\n"
    "Hãy tạo input TỐI ƯU RIÊNG cho từng nhóm công cụ:\n\n"
    "CÔNG CỤ 1 & 2 — tìm trong PAGE_SECTIONS (bài PHÂN TÍCH, BÁO CÁO, nhận định dài):\n"
    "- Vector Search (page_sections): tìm bằng embedding similarity.\n"
    "  Input là embedding_text — đây là đoạn text được chuyển thành vector.\n"
    "  Nó sẽ được dùng chung cho cả vector_search và event_search.\n"
    "- Keyword Search (page_sections): tìm bằng full-text search PostgreSQL.\n"
    "  Input là page_search_query — dùng THUẬT NGỮ CHUYÊN NGÀNH, khái niệm phân tích.\n\n"
    "CÔNG CỤ 3 & 4 — tìm trong EVENT_OBSERVATIONS (SỰ KIỆN CÓ TIMESTAMP, quan sát ngắn):\n"
    "- Event Vector Search: tìm bằng embedding similarity (dùng chung embedding_text).\n"
    "- Event Keyword Search: tìm bằng full-text search.\n"
    "  Input là event_search_query — dùng TÊN RIÊNG, SỰ KIỆN CỤ THỂ, SỐ LIỆU.\n\n"
    "QUY TẮC TẠO INPUTS:\n"
    "- embedding_text: 100-250 ký tự, dùng định dạng có CẤU TRÚC:\n"
    '  "query: <câu hỏi viết lại, mật độ từ khóa cao, bỏ stop-words>\n'
    "   keywords: <từ khóa VI> | <từ khóa EN> | <thuật ngữ chuyên ngành EN>\"\n"
    "  Đây là text dùng để EMBED (vector hóa). Tách biệt rõ 2 phần:\n"
    "  - query: câu semantic chính để bge-m3 match meaning\n"
    "  - keywords: từ khóa bổ trợ VI+EN để tăng recall cho cả nội dung tiếng Việt\n"
    "    và tiếng Anh. Chỉ liệt kê keyword, không viết thành câu.\n"
    "  Ví dụ: hỏi \"giá vàng hôm nay\" → embedding_text:\n"
    '  "query: giá vàng hôm nay cập nhật mới nhất biến động\n'
    '   keywords: vàng | gold price | XAU USD | kim loại quý | precious metals"\n'
    "- page_search_query: các từ/cụm nối bằng \" | \" (OR logic).\n"
    "  Tập trung THUẬT NGỮ CHUYÊN NGÀNH, KHÁI NIỆM PHÂN TÍCH.\n"
    "  Ví dụ: \"ngân hàng | bank | lãi suất | tín dụng | room tín dụng | phân tích ngành\"\n"
    "- event_search_query: các từ/cụm nối bằng \" | \" (OR logic).\n"
    "  Tập trung TÊN RIÊNG, SỰ KIỆN CỤ THỂ, SỐ LIỆU, MỐC THỜI GIAN.\n"
    '  Ví dụ: "VCB | BID | CTG | tăng lãi suất | room tín dụng | NHNN | tăng vốn"\n\n'
    "4. ENTITIES: thực thể được nhắc đến.\n"
    "Loại: stock_ticker, commodity, location, macro_indicator, person, organization, policy.\n"
    'Mỗi entity: {"name": "...", "type": "..."}\n\n'
    "5. SUB_QUESTIONS: nếu câu hỏi PHỨC TẠP (nhiều khía cạnh), phân rã thành 2-4 câu\n"
    "hỏi con ĐƠN GIẢN, ĐỘC LẬP. Nếu câu hỏi đơn giản → mảng rỗng [].\n\n"
    '6. LANGUAGE: "vi" nếu tiếng Việt, "en" nếu tiếng Anh.\n\n'
    # ═══════════════════════════════════════════════════════════════════════
    # OUTPUT FORMAT
    # ═══════════════════════════════════════════════════════════════════════
    "OUTPUT JSON (KHÔNG markdown, KHÔNG text ngoài JSON):\n"
    "{\n"
    '  "allowed": true hoặc false,\n'
    '  "reason": "lý do từ chối (chỉ khi allowed=false, nếu không để rỗng)",\n'
    '  "intent": "current_state|historical|timeline|comparative|factual_listing|general",\n'
    '  "language": "vi hoặc en",\n'
    '  "time_range": {"start": "YYYY-MM-DD hoặc null", "end": "YYYY-MM-DD hoặc null"},\n'
    '  "entities": [{"name": "tên", "type": "loại"}],\n'
    '  "embedding_text": "text tối ưu để embed, VI+EN",\n'
    '  "page_search_query": "từ khóa cho page_sections, nối bằng |",\n'
    '  "event_search_query": "từ khóa cho event_observations, nối bằng |",\n'
    '  "sub_questions": ["câu hỏi con 1", "câu hỏi con 2"]\n'
    "}\n\n"
    "CHỈ output JSON, không markdown, không giải thích."
)


def _extract_json(text: str) -> dict:
    """Extract a JSON object from *text*, even when wrapped in reasoning text."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

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
                except json.JSONDecodeError as exc:
                    raise ValueError("Unbalanced JSON braces in response") from exc
    raise ValueError("No balanced JSON object found in response")


class LLMGuardrailAnalyzerAdapter(GuardrailAnalyzerPort):
    """Guardrail + intent analysis + per-tool search inputs via a single LLM call.

    Falls back to ``GuardrailAnalysis(allowed=True, intent="general")`` on any
    failure — analysis is a quality improvement, not a hard dependency.
    """

    def __init__(
        self,
        llm: LLMClientPort,
        temperature: float = 0.0,
        max_tokens: int = 600,
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
            tz = get_system_tz()
            start = datetime.fromisoformat(start_str).replace(tzinfo=tz)
            end = (
                datetime.fromisoformat(end_str).replace(tzinfo=tz)
                if end_str
                else now()
            )
            return TimeRange(start=start, end=end)
        except (ValueError, TypeError):
            return None

    async def analyze(self, question: str) -> GuardrailAnalysis:
        try:
            messages = [
                {"role": "system", "content": _GUARDRAIL_ANALYZE_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Hôm nay là {now().strftime('%Y-%m-%d')}. "
                        f"Câu hỏi: {question}"
                    ),
                },
            ]
            raw = await self._llm.chat_completion(
                messages=messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                enable_thinking=False,
            )
            parsed = _extract_json(raw)

            allowed = parsed.get("allowed", True)
            reason = parsed.get("reason", "") if not allowed else ""

            if not allowed:
                return GuardrailAnalysis(
                    allowed=False,
                    reason=reason,
                    language=parsed.get("language", "vi"),
                )

            intent = parsed.get("intent", "general")
            time_range = self._parse_time_range(parsed.get("time_range"))
            language = parsed.get("language", "vi")

            raw_entities = parsed.get("entities", [])
            entities: list[dict] = []
            for ent in raw_entities:
                if isinstance(ent, str):
                    entities.append({"name": ent, "type": None})
                elif isinstance(ent, dict):
                    entities.append(
                        {"name": ent.get("name", ""), "type": ent.get("type")}
                    )

            embedding_text = str(parsed.get("embedding_text", "") or "").strip()
            page_search_query = str(
                parsed.get("page_search_query", "") or ""
            ).strip()
            event_search_query = str(
                parsed.get("event_search_query", "") or ""
            ).strip()

            sub_questions: list[str] = [
                str(sq).strip()
                for sq in parsed.get("sub_questions", [])
                if sq and str(sq).strip()
            ]

            logger.debug(
                "Guardrail analysis: allowed=%s intent=%s lang=%s "
                "time_range=%s entities=%d emb_text_len=%d "
                "page_q=%s event_q=%s sub_qs=%d",
                allowed,
                intent,
                language,
                time_range,
                len(entities),
                len(embedding_text),
                page_search_query[:80] if page_search_query else "(empty)",
                event_search_query[:80] if event_search_query else "(empty)",
                len(sub_questions),
            )
            return GuardrailAnalysis(
                allowed=True,
                intent=intent,
                language=language,
                time_range=time_range,
                entities=entities,
                embedding_text=embedding_text,
                page_search_query=page_search_query,
                event_search_query=event_search_query,
                sub_questions=sub_questions,
            )

        except Exception as exc:
            logger.warning(
                "Guardrail analysis failed, defaulting to general: %s", exc
            )
            return GuardrailAnalysis(
                allowed=True,
                intent="general",
                language="vi",
                entities=[],
            )
