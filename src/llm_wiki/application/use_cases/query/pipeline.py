import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

from llm_wiki.application.dto.query_dto import QueryInput
from llm_wiki.application.ports.search.event_search_port import EventSearchPort
from llm_wiki.application.ports.search.graph_rag_port import GraphRAGPort
from llm_wiki.application.ports.search.guardrail_analyzer_port import (
    GuardrailAnalysis,
    GuardrailAnalyzerPort,
)
from llm_wiki.application.ports.search.query_rewriter_port import (
    QueryRewriterPort,  # noqa: F401 — kept for backward compat, no longer wired
)
from llm_wiki.application.ports.search.vector_search import (
    CacheServicePort,
    EmbeddingServicePort,
    KeywordSearchPort,
    LLMClientPort,
    VectorSearchPort,
)
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange
from llm_wiki.infrastructure.telemetry.business_metrics import inc_counter
from llm_wiki.infrastructure.telemetry.metrics_collector import get_metrics
from llm_wiki.shared.datetime_utils import get_system_tz, now

root_logger = logging.getLogger()
logger = logging.getLogger(__name__)

# ── Adaptive recency decay (Phase 1) ──────────────────────────────────
# Different intents need different freshness horizons.
# current_state: half-life ~14 days  |  timeline / historical: no decay
# comparative: mild ~138 days        |  general: default ~69 days

_RECENCY_LAMBDA_MAP: dict[str, float] = {
    "current_state": 0.05,  # ~14 ngày half-life
    "general": 0.01,  # ~69 ngày
    "historical": 0.0,  # không decay — cần thông tin cũ
    "timeline": 0.0,  # không decay — diễn biến cần mọi thời điểm
    "comparative": 0.005,  # ~138 ngày
}


def _build_keyword_query(question: str, analysis: GuardrailAnalysis | None = None) -> str:
    """Build an effective keyword-search query from the question + analysis.

    When the analyzer provides ``page_search_query``, use it directly (it is
    already an OR-delimited, bilingual string optimised for page section search).

    Falls back to ``event_search_query``, then raw question.

    Note: callers should prefer using ``analysis.page_search_query`` for
    ``keyword_search`` and ``analysis.event_search_query`` for
    ``event_keyword_search`` directly.  This function is a convenience
    for the common case where both streams share the same keyword input.
    """
    if analysis and analysis.page_search_query:
        return analysis.page_search_query

    if analysis and analysis.event_search_query:
        return analysis.event_search_query

    # Fallback: raw question (existing behavior)
    return question


def _build_event_kw_query(question: str, analysis: GuardrailAnalysis | None = None) -> str:
    """Build the keyword query for event_keyword_search.

    Uses ``analysis.event_search_query`` when available; falls back to
    ``page_search_query``, then raw question.
    """
    if analysis and analysis.event_search_query:
        return analysis.event_search_query

    if analysis and analysis.page_search_query:
        return analysis.page_search_query

    return question


def recency_decay_for_intent(intent: str) -> float:
    """Return the recency decay rate (λ) appropriate for *intent*."""
    return _RECENCY_LAMBDA_MAP.get(intent, 0.01)


# ── Cache tuning constants (P3: variable TTL) ──────────────────────────

_SHORT_TTL = 3600  # 1 hour — answers that depend on "today" / "this week"
_LONG_TTL = 86400  # 24 hours — factual answers that rarely change
_SEMANTIC_THRESHOLD = 0.80  # cosine similarity floor for semantic cache hit
# Tuned to 0.80 after empirical testing: paraphrased Vietnamese questions with
# same intent typically range 0.80–0.88 against the stored embedding.
# A higher threshold (0.95) only catches near-identical strings, defeating
# the purpose of semantic cache.

_TIME_SENSITIVE_PATTERNS: list[str] = [
    r"hôm\s*nay",
    r"today",
    r"hôm\s*qua",
    r"yesterday",
    r"ngày\s*mai",
    r"tomorrow",
    r"tuần\s*này",
    r"this\s+week",
    r"tháng\s*này",
    r"this\s+month",
    r"năm\s*nay",
    r"this\s+year",
    r"mới\s*nhất",
    r"latest",
    r"gần\s*đây",
    r"recent(?:ly)?",
    r"hiện\s*tại",
    r"currently",
    r"now",
    r"bây\s*giờ",
]

_TIME_PATTERNS = [
    # Vietnamese — explicit time ranges (high confidence)
    (r"(\d+)\s*ngày\s*(vừa\s*)?qua", lambda m: timedelta(days=int(m.group(1)))),
    (r"(\d+)\s*tuần\s*(vừa\s*)?qua", lambda m: timedelta(weeks=int(m.group(1)))),
    (r"(\d+)\s*tháng\s*(vừa\s*)?qua", lambda m: timedelta(days=int(m.group(1)) * 30)),
    (
        r"(?:trong\s+)?(?:khoảng\s+)(\d+)\s*tháng\s+(?:qua|vừa\s+qua|gần\s+đây)",
        lambda m: timedelta(days=int(m.group(1)) * 30),
    ),
    # English — explicit numeric time ranges
    (r"(?:the\s+)?(?:past|last)\s+(\d+)\s*days?", lambda m: timedelta(days=int(m.group(1)))),
    (r"(?:the\s+)?(?:past|last)\s+(\d+)\s*weeks?", lambda m: timedelta(weeks=int(m.group(1)))),
    (r"(?:the\s+)?(?:past|last)\s+(\d+)\s*months?", lambda m: timedelta(days=int(m.group(1)) * 30)),
    (r"(?:the\s+)?(?:past|last)\s+(\d+)\s*years?", lambda m: timedelta(days=int(m.group(1)) * 365)),
    # Explicit "past/last month/week/year" (without numbers)
    (r"(?:the\s+)?(?:past|last)\s+month(?!\s*\d)", lambda m: timedelta(days=30)),
    (r"(?:the\s+)?(?:past|last)\s+week(?!\s*\d)", lambda m: timedelta(days=7)),
    (r"(?:the\s+)?(?:past|last)\s+year(?!\s*\d)", lambda m: timedelta(days=365)),
    # Vietnamese — bare relative time words (hôm qua, tuần trước, ...)
    (r"hôm\s+qua|yesterday", lambda m: timedelta(days=1)),
        (r"tuần\s+trước", lambda m: _prev_calendar_week(m)),
    (r"tháng\s+trước", lambda m: _prev_calendar_month(m)),
    (r"năm\s+ngoái", lambda m: _prev_calendar_year(m)),
    (r"quý\s+(\d)\s+năm\s+(\d{4})", lambda m: _quarter_delta(m)),
        (r"quý\s+này|this\s+quarter", lambda m: _current_quarter(m)),
    (r"quý\s+trước|quý\s+vừa\s+qua|last\s+quarter", lambda m: _prev_calendar_quarter(m)),
    # Explicit "this period" — medium confidence, use generous range
    (r"tuần\s*này", lambda m: timedelta(days=7)),
    (r"tháng\s*này", lambda m: timedelta(days=60)),
    (r"năm\s*nay", lambda m: timedelta(days=365)),
    (r"this\s+week", lambda m: timedelta(days=7)),
    (r"this\s+month", lambda m: timedelta(days=60)),
    (r"this\s+year", lambda m: timedelta(days=365)),
    # Low-confidence patterns — these are often contextual, not strict
    # "recent/gần đây/dạo này" are intentionally broad
    (r"(?:in\s+the\s+)?recent\s*(?:days?|times?)?", lambda m: timedelta(days=30)),
    (r"gần\s*đây|dạo\s*này|mới\s*đây", lambda m: timedelta(days=30)),
    # "today" is specific enough
    (r"hôm\s*nay|today", lambda m: timedelta(days=1)),
    # "hiện tại/now/currently" — DO NOT filter, these are conversational cues
    # not temporal constraints. Recency decay handles prioritization naturally.
    # Numeric date ranges like "2024", "Jan 2024"
    (
        r"(?:in\s+)?(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
        r"may|june?|july?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+(\d{4})",
        lambda m: _month_year_delta(m),
    ),
    (r"(?:in\s+)?(\d{4})", lambda m: _year_delta(m)),
]

_MONTH_MAP = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _month_year_delta(m: re.Match) -> timedelta | None:
    month_str, year_str = m.group(1).lower()[:3], int(m.group(2))
    if month_str not in _MONTH_MAP:
        return None
    from datetime import date

    month_num = _MONTH_MAP[month_str]
    ref_date = date(year_str, month_num, 1)
    now = date.today()
    if ref_date > now:
        return None
    return timedelta(days=(now - ref_date).days + 31)


def _year_delta(m: re.Match) -> timedelta | None:
    year = int(m.group(1))
    if year < 2000 or year > now().year:
        return None
    start = datetime(year, 1, 1, tzinfo=now().tzinfo)
    return timedelta(days=(now() - start).days + 1)


def _prev_calendar_week(_m: re.Match) -> TimeRange | None:
    """Previous calendar week (Mon 00:00 → Sun 23:59:59.999999)."""
    today = now()
    this_monday = (today - timedelta(days=today.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return TimeRange(start=this_monday - timedelta(days=7), end=this_monday - timedelta(microseconds=1))


def _current_quarter(_m: re.Match) -> TimeRange | None:
    """Current calendar quarter start → now."""
    today = now()
    q_start_month = ((today.month - 1) // 3) * 3 + 1
    return TimeRange(start=datetime(today.year, q_start_month, 1, tzinfo=today.tzinfo), end=today)


def _prev_calendar_quarter(_m: re.Match) -> TimeRange | None:
    """Previous calendar quarter boundaries."""
    today = now()
    q_start_month = ((today.month - 1) // 3) * 3 + 1
    start = datetime(today.year, q_start_month, 1, tzinfo=today.tzinfo)
    prev_end = start - timedelta(microseconds=1)
    prev_start_month = ((prev_end.month - 1) // 3) * 3 + 1
    return TimeRange(
        start=datetime(prev_end.year, prev_start_month, 1, tzinfo=today.tzinfo),
        end=prev_end,
    )


def _prev_calendar_month(_m: re.Match) -> TimeRange | None:
    """Previous calendar month boundaries."""
    today = now()
    start = datetime(today.year, today.month, 1, tzinfo=today.tzinfo)
    prev_end = start - timedelta(microseconds=1)
    return TimeRange(
        start=datetime(prev_end.year, prev_end.month, 1, tzinfo=today.tzinfo),
        end=prev_end,
    )


def _prev_calendar_year(_m: re.Match) -> TimeRange | None:
    """Previous calendar year boundaries."""
    today = now()
    start = datetime(today.year, 1, 1, tzinfo=today.tzinfo)
    prev_end = start - timedelta(microseconds=1)
    return TimeRange(start=datetime(prev_end.year, 1, 1, tzinfo=today.tzinfo), end=prev_end)


_TIME_KEYWORDS = re.compile(
    r"when|recent|past|last|ago|today|yesterday|week|month|year|"
    r"khi|nào|lúc|gần|đây|hôm|nay|qua|tuần|tháng|năm|trước|vừa|mới|dạo|"
    r"202[0-9]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec",
    re.IGNORECASE,
)


def _extract_time_range(question: str) -> TimeRange | None:
    for pattern, delta_fn in _TIME_PATTERNS:
        m = re.search(pattern, question, re.IGNORECASE)
        if not m:
            continue
        try:
            delta = delta_fn(m)
            if delta is not None:
                if isinstance(delta, TimeRange):
                    return delta
                return TimeRange(start=now() - delta, end=now())
        except Exception:
            continue
    return None


def _may_be_time_related(question: str) -> bool:
    return bool(_TIME_KEYWORDS.search(question))


# ── Intent-driven retrieval weights (from 29_LLM_wiki production) ────────
# Timeline questions emphasize events, general questions emphasize sections.

INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "current_state": {"events": 1.0, "sections": 0.7},
    "historical": {"events": 0.8, "sections": 0.5},
    "timeline": {"events": 1.0, "sections": 0.3},
    "comparative": {"events": 0.5, "sections": 0.8},
    "general": {"events": 0.4, "sections": 1.0},
}

# Keyword streams get this fraction of their corresponding vector stream weight.
# A 50 % discount reflects that keyword search is supplementary — it reinforces
# vector results but should not dominate RRF fusion.
_KW_DISCOUNT = 0.5


def _build_rrf_weights(intent: str) -> dict[str, float]:
    """Return per-stream RRF weights driven by query intent.

    Keyword stream weights are derived from their vector counterparts
    multiplied by ``_KW_DISCOUNT``, keeping all streams intent-consistent.
    Graph weight is handled separately by callers.
    """
    iw = INTENT_WEIGHTS.get(intent, INTENT_WEIGHTS["general"])
    return {
        "sections": iw["sections"],
        "events": iw["events"],
        "keyword_sections": iw["sections"] * _KW_DISCOUNT,
        "keyword_events": iw["events"] * _KW_DISCOUNT,
    }


def _weighted_rrf_fusion(
    ranked_sets: dict[str, list[SearchResult]],
    weights: dict[str, float],
    k: int = 60,
) -> list[SearchResult]:
    """Merge ranked lists with per-stream weights via Reciprocal Rank Fusion.

    Each stream's weight multiplies its RRF score: ``weight * 1/(k+rank)``.
    Streams not present in *weights* default to weight 0.0 (excluded).
    """
    scores: dict[str, tuple[SearchResult, float]] = {}
    for set_name, results in ranked_sets.items():
        w = weights.get(set_name, 0.0)
        if w <= 0.0 or not results:
            continue
        for rank, result in enumerate(results, start=1):
            if result.content_id not in scores:
                scores[result.content_id] = (result, 0.0)
            _, existing = scores[result.content_id]
            scores[result.content_id] = (result, existing + w * 1.0 / (k + rank))

    sorted_items = sorted(scores.values(), key=lambda x: x[1], reverse=True)
    return [item[0] for item in sorted_items]


def _get_relevant_timestamp(r: SearchResult) -> datetime | None:
    """Extract the most semantically relevant timestamp from a SearchResult.

    For event observations, uses ``normalized_date`` (actual event date).
    For page sections, uses ``published_at`` (content publication date).
    Returns None when no parseable date is found in metadata.
    """
    if not r.metadata:
        return None

    if r.content_type == "event_observation":
        date_str = r.metadata.get("normalized_date") or r.metadata.get("event_date")
    else:
        date_str = r.metadata.get("published_at")

    if not date_str:
        return None

    try:
        date_str_clean = str(date_str)[:10]
        return datetime.strptime(date_str_clean, "%Y-%m-%d").replace(tzinfo=get_system_tz())
    except (ValueError, TypeError):
        return None


def _enforce_time_boundary(
    results: list[SearchResult],
    time_range: TimeRange | None,
) -> list[SearchResult]:
    """Hard-filter results to only those whose date falls within *time_range*.

    Uses ``_get_relevant_timestamp`` to determine the semantically correct
    date for each result (event occurrence date for events, publication date
    for page sections).  Results without a parseable date pass through
    (cannot prove they're out of range).

    When *time_range* is None, returns all results unchanged — no time
    constraint means no filtering.
    """
    if time_range is None:
        return results

    filtered: list[SearchResult] = []
    for r in results:
        relevant_date = _get_relevant_timestamp(r)
        if relevant_date is None:
            filtered.append(r)
            continue

        if time_range.start <= relevant_date <= time_range.end:
            filtered.append(r)

    return filtered


# ── Language detection ─────────────────────────────────────────────────
# Zero-token regex-based detection. The analyzer also outputs "language",
# but this regex fallback covers the case where the analyzer fails or is
# disabled.  Vietnamese is detected by the presence of diacritic characters
# that are unique to the Vietnamese alphabet.
_VI_DIACRITICS = re.compile(
    r"[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôồốỗổộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]"
)


def _detect_language(question: str) -> str:
    """Return ``"vi"`` if the question contains Vietnamese diacritics, else ``"en"``.

    Edge case: Vietnamese-without-diacritics (e.g. "cho toi biet ve AI") will
    be classified as ``"en"``. The query analyzer's ``language`` field is the
    authoritative source; this regex serves as a zero-token fallback.
    """
    return "vi" if _VI_DIACRITICS.search(question.lower()) else "en"


_EN_TEMPORAL_ADDENDUM: dict[str, str] = {
    "timeline": (
        "\n\nNOTE: The user needs a CHRONOLOGICAL TIMELINE. "
        "Sort the answer by chronological order. "
        "Each milestone MUST have a specific date (day-month-year)."
    ),
    "historical": (
        "\n\nNOTE: The user is asking about PAST EVENTS. "
        "Include specific dates for all information."
    ),
    "current_state": (
        "\n\nNOTE: The user is asking about the CURRENT STATE. "
        "Prioritize the most recent information, include specific dates."
    ),
    "comparative": (
        "\n\nNOTE: The user wants a COMPARISON. "
        "Present a clear side-by-side comparison with dates for each data point."
    ),
}

_VI_TEMPORAL_ADDENDUM: dict[str, str] = {
    "timeline": (
        "\n\nLƯU Ý: Ngườdùng cần DIỄN BIẾN THEO THỜI GIAN. "
        "Sắp xếp câu trả lờtheo trình tự thờgian. "
        "Mỗi mốc phải có ngày tháng năm cụ thể."
    ),
    "historical": (
        "\n\nLƯU Ý: Ngườdùng hỏi về SỰ KIỆN TRONG QUÁ KHỨ. "
        "Ghi rõ ngày tháng năm cụ thể cho mọi thông tin."
    ),
    "current_state": (
        "\n\nLƯU Ý: Ngườdùng hỏi về TÌNH HÌNH HIỆN TẠI. "
        "Ưu tiên thông tin mớnhất, ghi rõ ngày tháng."
    ),
    "comparative": (
        "\n\nLƯU Ý: Ngườdùng muốn SO SÁNH. "
        "Trình bày đối chiếu rõ ràng, ghi rõ thờđiểm của từng dữ liệu."
    ),
}


def _temporal_addendum(intent: str, language: str = "vi") -> str:
    """Return intent-specific temporal instructions for the LLM synthesis prompt."""
    if language == "en":
        return _EN_TEMPORAL_ADDENDUM.get(intent, "")
    return _VI_TEMPORAL_ADDENDUM.get(intent, "")


# ── Shared synthesis prompt builder (used by QueryPipeline + SelfReflectiveRAGPipeline) ──

# Persona + structure (general intent fallback; intent-specific pipelines override the persona)
_SYNTHESIS_PERSONA_EN = (
    "You are a deep-research assistant. "
    "Answer the question based ON THE PROVIDED CONTEXT. "
    "Your answer MUST include: "
    "(1) a concise summary at the beginning; "
    "(2) detailed analysis of each relevant aspect, "
    "with specific examples from the context; "
    "(3) explanation of connections between ideas; "
    "(4) an overall conclusion at the end. "
)

_SYNTHESIS_PERSONA_VI = (
    "Bạn là một trợ lý nghiên cứu chuyên sâu. "
    "Hãy trả lời câu hỏi dựa TRÊN NGỮ CẢNH được cung cấp. "
    "Câu trả lời phải bao gồm: "
    "(1) một câu tóm tắt ngắn gọn ở đầu; "
    "(2) phân tích chi tiết từng khía cạnh liên quan, "
    "kèm ví dụ cụ thể từ ngữ cảnh; "
    "(3) giải thích mối liên hệ giữa các ý; "
    "(4) kết luận tổng thể ở cuối. "
)

# Shared boilerplate — citation rules + today's date + temporal precision constraints.
# Used by general QueryPipeline (prepended with persona above) and by
# SelfReflectiveRAGPipeline (prepended with intent-specific persona).
SYNTHESIS_BOILERPLATE_EN = (
    "Cite sources using [N]. "
    "If the context is insufficient, clearly state which parts "
    "are missing rather than fabricating. "
    "Be thorough and concise, not superficial.\n\n"
    "IMPORTANT: Today is {today}. "
    "When answering, you MUST include specific dates (day-month-year) "
    "for all information. "
    "DO NOT use relative terms like 'recently', 'a few months ago'. "
    "Always cite exact dates from the provided context."
)

SYNTHESIS_BOILERPLATE_VI = (
    "Trích dẫn nguồn bằng [N]. "
    "Nếu ngữ cảnh không đủ, hãy nêu rõ phần nào chưa có thông tin "
    "thay vì bịa đặt. "
    "Hãy trả lời đầy đủ, súc tích nhưng không sơ xài.\n\n"
    "LƯU Ý QUAN TRỌNG: Hôm nay là {today}. "
    "Khi trả lời, PHẢI ghi rõ ngày tháng năm cụ thể cho mọi thông tin. "
    "KHÔNG được dùng từ tương đối như 'gần đây', 'mới đây', "
    "'cách đây vài tháng', 'trong thời gian gần đây'. "
    "Luôn trích dẫn ngày tháng chính xác từ ngữ cảnh được cung cấp."
)

# Full base prompts (persona + boilerplate) for the general QueryPipeline path.
_SYNTHESIS_BASE_EN = _SYNTHESIS_PERSONA_EN + SYNTHESIS_BOILERPLATE_EN
_SYNTHESIS_BASE_VI = _SYNTHESIS_PERSONA_VI + SYNTHESIS_BOILERPLATE_VI


def _build_synthesis_prompt(language: str, today_str: str, intent: str = "general") -> str:
    """Build the synthesis system prompt for the given language and today's date.

    Shared by QueryPipeline (execute + execute_stream) and
    SelfReflectiveRAGPipeline (_build_messages).
    """
    base = _SYNTHESIS_BASE_EN if language == "en" else _SYNTHESIS_BASE_VI
    prompt = base.format(today=today_str)
    prompt += _temporal_addendum(intent, language=language)
    return prompt


def _set_parent_on_wrappers(
    embedder: EmbeddingServicePort,
    vector_search: VectorSearchPort,
    keyword_search: KeywordSearchPort,
    llm: LLMClientPort,
    cache: CacheServicePort,
    parent: TelemetrySpan,
    rewriter: QueryRewriterPort | None = None,
    analyzer: GuardrailAnalyzerPort | None = None,
    event_search: EventSearchPort | None = None,
) -> None:
    """Wire the pipeline root span as parent for all traced wrappers.

    Each wrapper that exposes ``set_parent_span()`` will attach its own spans
    under *parent*, building a single tree on LangSmith.
    """
    for wrapped in (
        embedder,
        vector_search,
        keyword_search,
        llm,
        cache,
        rewriter,
        analyzer,
        event_search,
    ):
        if wrapped is None:
            continue
        fn = getattr(wrapped, "set_parent_span", None)
        if callable(fn):
            fn(parent)


async def _timed(fn) -> tuple[Any, float]:
    """Run *fn* (sync or async) and return ``(result, latency_seconds)``.

    This is a pure timing helper — tracing is handled exclusively by the
    traced wrappers (TracedLLMWrapper, TracedEmbeddingWrapper, etc.).
    """
    t0 = time.time()
    if callable(fn):
        called = fn()
        if asyncio.iscoroutine(called):
            result = await called
        else:
            result = called
    else:
        result = fn
    return result, time.time() - t0


class QueryPipeline:
    def __init__(
        self,
        embedder: EmbeddingServicePort,
        vector_search: VectorSearchPort,
        keyword_search: KeywordSearchPort,
        llm: LLMClientPort,
        cache: CacheServicePort,
        telemetry: TelemetryPort | None = None,
        rewriter: QueryRewriterPort | None = None,
        analyzer: GuardrailAnalyzerPort | None = None,
        event_search: EventSearchPort | None = None,
        graph_rag: GraphRAGPort | None = None,
    ):
        self._embedder = embedder
        self._vector_search = vector_search
        self._keyword_search = keyword_search
        self._llm = llm
        self._cache = cache
        self._telemetry = telemetry
        self._rewriter = rewriter  # no longer active — kept for backward compat
        self._analyzer = analyzer
        self._event_search = event_search
        self._graph_rag = graph_rag

    # ── Cache helpers (P1: normalization, P3: variable TTL) ────────────

    @staticmethod
    def _normalize_question(question: str) -> str:
        """Normalize a question for consistent exact-cache keys.

        Strips punctuation, collapses whitespace, lowercases — so
        "Ai là CEO Apple?" and "ai là ceo apple" produce the same hash.
        """
        q = question.lower().strip()
        q = re.sub(r"[^\w\s]", "", q)  # remove punctuation
        q = re.sub(r"\s+", " ", q)  # collapse whitespace
        return q

    @staticmethod
    def _is_time_sensitive(question: str) -> bool:
        """Return True when the question implies a temporal constraint.

        Questions like "what happened today" or "latest news" should use a
        short TTL since the answer may be stale quickly.
        """
        q_lower = question.lower()
        return any(re.search(p, q_lower) for p in _TIME_SENSITIVE_PATTERNS)

    def _cache_key(self, question: str, source_id: str | None = None) -> str:
        """Build an exact-match cache key.

        Uses SHA256 of the normalized question.  Date is NOT embedded in the
        key — content invalidation is handled by the cache TTL, which is
        shorter for time-sensitive questions (see ``_cache_ttl``).

        *source_id* is included when non-None so that queries scoped to
        different sources never share a cache entry.
        """
        normalized = self._normalize_question(question)
        scope = f":src-{source_id}" if source_id else ""
        return f"qa:v3:{hashlib.sha256(normalized.encode()).hexdigest()}{scope}"

    def _cache_ttl(self, question: str) -> int:
        """Pick an appropriate TTL based on time-sensitivity.

        - Time-sensitive questions ("hôm nay", "this week") → short TTL (1h)
        - Factual questions without temporal cues → long TTL (24h)
        """
        return _SHORT_TTL if self._is_time_sensitive(question) else _LONG_TTL

    # ── Exact cache hit helper ─────────────────────────────────────────

    async def _try_exact_cache(self, question: str, source_id: str | None = None) -> dict | None:
        """Check exact-match cache. Returns parsed cached data or None on miss."""
        cache_key = self._cache_key(question, source_id=source_id)
        cached = await self._cache.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                logger.debug("Exact cache hit but JSON decode failed, ignoring")
        return None

    async def _try_semantic_cache(self, embedding: Embedding) -> dict | None:
        """Check semantic (embedding similarity) cache.

        Returns parsed cached data or None.  Degrades gracefully.
        """
        try:
            raw = await self._cache.semantic_get(embedding.vector, _SEMANTIC_THRESHOLD)
            if raw:
                return json.loads(raw)
        except Exception:
            logger.debug("Semantic cache lookup failed, ignoring", exc_info=True)
        return None

    def _build_cached_stream_event(self, data: dict) -> dict:
        """Build a ``complete`` event from cached data, suitable for streaming output."""
        return {
            "type": "complete",
            "data": {
                "answer": data.get("answer", ""),
                "citations": data.get("sources", data.get("citations", [])),
                "sources_used": data.get("sources_used", []),
                "tokens_used": 0,
                "cache_hit": True,
            },
        }

    async def _extract_time_range_with_llm(self, question: str) -> TimeRange | None:
        prompt = (
            "Extract the time range from this question. "
            'Return ONLY a JSON object: {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"} '
            'or {"start_date": null, "end_date": null} if no time range is implied. '
            'Use "now" as end_date for relative times like "past month" or "recent". '
            f"Today is {now().strftime('%Y-%m-%d')}. "
            f"Question: {question}"
        )
        try:
            raw = await self._llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=128,
            )
            data = json.loads(raw.strip())
            if data.get("start_date"):
                tz = get_system_tz()
                start = datetime.fromisoformat(data["start_date"]).replace(tzinfo=tz)
                end_str = data.get("end_date", "now")
                end = (
                    now() if end_str == "now"
                    else datetime.fromisoformat(end_str).replace(tzinfo=tz)
                )
                return TimeRange(start=start, end=end)
        except Exception:
            logger.debug("LLM time extraction failed")
        return None

    def _resolve_time_range(self, input: QueryInput) -> TimeRange | None:
        if input.from_date or input.to_date:
            min_dt = datetime.min.replace(tzinfo=get_system_tz())
            return TimeRange(start=input.from_date or min_dt, end=input.to_date)
        tr = _extract_time_range(input.question)
        if tr:
            return tr
        return None

    async def _resolve_time_range_async(self, input: QueryInput) -> TimeRange | None:
        tr = self._resolve_time_range(input)
        if tr:
            return tr
        if _may_be_time_related(input.question):
            t0 = time.time()
            tr = await self._extract_time_range_with_llm(input.question)
            logger.debug("LLM time extraction took %.2fs, result=%s", time.time() - t0, tr)
            return tr
        return None

    def _reciprocal_rank_fusion(
        self, ranked_lists: list[list[SearchResult]], k: int = 60
    ) -> list[SearchResult]:
        scores: dict[str, tuple[SearchResult, float]] = {}
        for results in ranked_lists:
            for rank, result in enumerate(results, start=1):
                if result.content_id not in scores:
                    scores[result.content_id] = (result, 0.0)
                _, score = scores[result.content_id]
                scores[result.content_id] = (result, score + 1.0 / (k + rank))

        sorted_items = sorted(scores.values(), key=lambda x: x[1], reverse=True)
        return [item[0] for item in sorted_items]

    def _build_context(self, results: list[SearchResult]) -> str:
        """Build context for LLM synthesis with full date + source + quality metadata.

        For event observations, includes the actual event occurrence date
        (normalized_date), observer stance, observation count, and sentiment
        score — giving the LLM richer signals to weigh source credibility and
        temporal relevance.
        """
        context_parts = []
        for i, result in enumerate(results[:20], start=1):
            source = result.metadata.get("source_name", "unknown") if result.metadata else "unknown"
            page_title = result.metadata.get("page_title", "") if result.metadata else ""
            published_at = result.metadata.get("published_at", "") if result.metadata else ""
            normalized_date = result.metadata.get("normalized_date", "") if result.metadata else ""
            event_date = result.metadata.get("event_date", "") if result.metadata else ""

            # Prefer normalized_date (actual event date) for event-type results;
            # fall back to event_date, then published_at.
            date_str = ""
            if normalized_date:
                date_str = f", ngày sự kiện: {str(normalized_date)[:10]}"
            elif event_date and isinstance(event_date, str):
                date_str = f", sự kiện: {event_date[:10]}"
            elif published_at and isinstance(published_at, str):
                date_str = f", {published_at[:10]}"
            elif published_at:
                date_str = f", {str(published_at)[:10]}"

            # Event-specific quality indicators
            observation_count = (
                result.metadata.get("observation_count") if result.metadata else None
            )
            stance = result.metadata.get("stance", "") if result.metadata else ""
            sentiment_score = result.metadata.get("sentiment_score") if result.metadata else None

            quality_parts = []
            if observation_count is not None:
                quality_parts.append(f"số quan sát: {observation_count}")
            if stance:
                quality_parts.append(f"quan điểm: {stance}")
            if sentiment_score is not None:
                quality_parts.append(f"cảm xúc: {sentiment_score:+.1f}")
            quality_str = f" ({'; '.join(quality_parts)})" if quality_parts else ""

            heading = f" {result.title}" if result.title else ""
            prefix = f"[{i}]{heading} (nguồn: {source}{date_str}){quality_str}"
            if page_title:
                prefix += f" (trang: {page_title})"
            context_parts.append(f"{prefix}\n{result.content}")
        return "\n\n".join(context_parts)

    async def _retrieve_and_merge(
        self,
        input: QueryInput,
        query_embedding: Embedding,
        time_range: TimeRange | None,
        intent: str = "general",
        rewritten_question: str | None = None,
        entities: list[dict] | None = None,
        analysis: GuardrailAnalysis | None = None,
    ) -> tuple[dict[str, list[SearchResult]], list[SearchResult], dict[str, float]]:
        """Run parallel multi-retrieval + weighted RRF fusion + diversity.

        Returns (all_result_sets, top_results_after_diversity, step_times).

        When *analysis* provides per-tool search queries, they are used
        separately: ``page_search_query`` for keyword_search and
        ``event_search_query`` for event_keyword_search.
        """
        step_times: dict[str, float] = {}
        search_query = rewritten_question or input.question

        # Build keyword-search queries — separate for page vs event when available
        kw_query = _build_keyword_query(search_query, analysis)
        event_kw_query = _build_event_kw_query(search_query, analysis)

        # Build parallel tasks — always at least vector + keyword sections
        async def _vec_sections():
            return await self._vector_search.search_similar(
                query_embedding,
                top_k=input.top_k * 2,
                source_id=input.source_id,
                time_range=time_range,
            )

        async def _kw_sections():
            return await self._keyword_search.search_keyword(
                kw_query,
                top_k=input.top_k,
                time_range=time_range,
            )

        tasks: list[tuple[str, Any]] = [
            ("sections", _timed(_vec_sections)),
            ("keyword_sections", _timed(_kw_sections)),
        ]

        if self._event_search:

            async def _vec_events():
                return await self._event_search.search_events(
                    query_embedding,
                    top_k=input.top_k * 2,
                    time_range=time_range,
                )

            async def _kw_events():
                return await self._event_search.search_events_keyword(
                    event_kw_query,
                    top_k=input.top_k,
                    time_range=time_range,
                )

            tasks.append(("events", _timed(_vec_events)))
            tasks.append(("keyword_events", _timed(_kw_events)))

        # GraphRAG: traverse knowledge graph when entities are detected
        if self._graph_rag and entities:

            async def _graph():
                return await self._graph_rag.traverse(
                    entities,
                    top_k=10,
                    time_range=time_range,
                )

            tasks.append(("graph", _timed(_graph)))

        # Run all tasks in parallel
        coros = [t[1] for t in tasks]
        results_raw = await asyncio.gather(*coros, return_exceptions=True)

        result_sets: dict[str, list[SearchResult]] = {}
        for (name, _), raw in zip(tasks, results_raw, strict=True):
            if isinstance(raw, Exception):
                logger.warning("Search stream '%s' failed: %s", name, raw)
                result_sets[name] = []
                step_times[name] = 0.0
            else:
                result_sets[name], step_times[name] = raw

        # Weighted RRF fusion (with graph weight)
        weights = _build_rrf_weights(intent)
        if "graph" in result_sets:
            weights["graph"] = 0.6
        merged = _weighted_rrf_fusion(result_sets, weights)

        # Hard time-boundary enforcement: when the user asks about a specific
        # time range, exclude results whose date falls outside it.
        in_range = _enforce_time_boundary(merged, time_range)
        top_results = in_range[: input.top_k]

        return result_sets, top_results, step_times

    async def execute(self, input: QueryInput) -> dict:
        step_times: dict[str, float] = {}
        cache_hit = False
        intent = "general"
        root_span: TelemetrySpan | None = None
        telemetry = self._telemetry

        if telemetry:
            root_span = await telemetry.start_span(
                name="rag_query",
                kind="chain",
                inputs={
                    "question": input.question,
                    "source_id": input.source_id,
                    "top_k": input.top_k,
                    "from_date": input.from_date.isoformat() if input.from_date else None,
                    "to_date": input.to_date.isoformat() if input.to_date else None,
                },
            )
            _set_parent_on_wrappers(
                self._embedder,
                self._vector_search,
                self._keyword_search,
                self._llm,
                self._cache,
                root_span,
                rewriter=self._rewriter,
                analyzer=self._analyzer,
                event_search=self._event_search,
            )

        # ── P1: exact-match cache with normalized key ──────────────────
        cached_data, step_times["cache_check"] = await _timed(
            lambda: self._try_exact_cache(input.question, source_id=input.source_id)
        )

        if cached_data:
            cache_hit = True
            inc_counter("query_total", {"status": "success", "cache": "exact"})
            cached_data["cache_hit"] = True
            cached_data["pipeline_steps"] = step_times
            answer_text = cached_data.get("answer", "")
            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span,
                    outputs={
                        "answer_length": len(answer_text),
                        "answer": answer_text,
                    },
                    metadata={
                        "cache_hit": True,
                        "cache_type": "exact",
                        "question": input.question,
                        "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                    },
                )
            return cached_data

        # ── Multi-turn: resolve follow-up pronouns via chat history ─────
        # The rewritten question drives analysis/retrieval; the original is
        # kept for user-facing output and cache keys.
        question = input.question
        if self._rewriter and input.chat_history:
            question = await self._rewriter.rewrite(input.question, input.chat_history)
            logger.debug("Rewritten follow-up: %r → %r", input.question[:80], question[:80])

        # ── Guardrail + Intent analysis (single LLM call, no rewrite) ──
        analysis = GuardrailAnalysis()
        if self._analyzer:
            analysis, step_times["analyze"] = await _timed(
                lambda: self._analyzer.analyze(question)
            )

            # Guardrail rejection: deny immediately, no search
            if not analysis.allowed:
                reject_answer = (
                    analysis.reason
                    or "Xin lỗi, tôi chỉ có thể trả lời các câu hỏi về "
                    "kinh tế, tài chính, chứng khoán, và đầu tư."
                )
                inc_counter("query_total", {"status": "rejected", "cache": "n/a"})
                if telemetry and root_span:
                    await telemetry.end_span(
                        span=root_span,
                        outputs={"answer_length": len(reject_answer), "answer": reject_answer},
                        metadata={
                            "allowed": False,
                            "reason": analysis.reason,
                            "question": input.question,
                            "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                        },
                    )
                return {
                    "answer": reject_answer,
                    "sources": [],
                    "tokens_used": 0,
                    "cache_hit": False,
                    "pipeline_steps": step_times,
                    "rejected": True,
                }

            intent = analysis.intent
            logger.debug(
                "Guardrail analysis: allowed=%s intent=%s time_range=%s entities=%d "
                "emb_text_len=%d page_q=%s event_q=%s",
                analysis.allowed,
                intent,
                analysis.time_range.start if analysis.time_range else None,
                len(analysis.entities),
                len(analysis.embedding_text),
                analysis.page_search_query[:80] if analysis.page_search_query else "(empty)",
                analysis.event_search_query[:80] if analysis.event_search_query else "(empty)",
            )

        # ── Embedding with analyzer-optimised text ────────────────────
        # Use embedding_text when available (dense keywords VI+EN); fall back to
        # raw question so the system still works without the analyzer.
        embed_text = analysis.embedding_text or question
        try:
            query_embedding, step_times["embed"] = await _timed(
                lambda: self._embedder.embed(embed_text),
            )
        except Exception as exc:
            inc_counter("query_total", {"status": "error", "cache": "n/a"})
            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span,
                    error=str(exc),
                    metadata={"error_type": type(exc).__name__},
                )
            raise

        # ── P2: semantic cache check ───────────────────────────────────
        sem_data, step_times["semantic_cache_check"] = await _timed(
            lambda: self._try_semantic_cache(query_embedding)
        )
        if sem_data:
            cache_hit = True
            inc_counter("query_total", {"status": "success", "cache": "semantic"})
            sem_data["cache_hit"] = True
            sem_data["pipeline_steps"] = step_times
            answer_text = sem_data.get("answer", "")
            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span,
                    outputs={
                        "answer_length": len(answer_text),
                        "answer": answer_text,
                    },
                    metadata={
                        "cache_hit": True,
                        "cache_type": "semantic",
                        "question": input.question,
                        "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                    },
                )
            return sem_data

        # ── Resolve time_range (analyzer → regex → LLM fallback) ─────
        time_range = analysis.time_range
        if time_range is None:
            if input.from_date or input.to_date:
                min_dt = datetime.min.replace(tzinfo=get_system_tz())
                time_range = TimeRange(start=input.from_date or min_dt, end=input.to_date)
            else:
                time_range = _extract_time_range(question)
                if time_range is None and _may_be_time_related(question):
                    time_range = await self._extract_time_range_with_llm(question)
        if time_range:
            logger.debug("Time range: %s → %s", time_range.start, time_range.end)

        # ── Multi-retrieval (parallel) + weighted RRF + diversity ───
        _, top_results, search_step_times = await self._retrieve_and_merge(
            input,
            query_embedding,
            time_range,
            intent=intent,
                rewritten_question=question,
            entities=analysis.entities if analysis.entities else None,
            analysis=analysis,
        )
        step_times.update(search_step_times)

        # ── Build context with dates ─────────────────────────────────
        context = self._build_context(top_results)

        # ── System prompt with language + temporal addendum + today's date ─
        lang = analysis.language or _detect_language(input.question)
        today_str = now().strftime("%Y-%m-%d")
        system_prompt = _build_synthesis_prompt(lang, today_str, intent)
        if input.chat_history:
            turns = input.chat_history[-6:]
            history_text = "\n".join(
                f"{'User' if t.get('role') == 'user' else 'Assistant'}: {t.get('content', '')}"
                for t in turns
            )
            system_prompt += (
                "\n\nCuộc hội thoại trước đó (tham khảo để hiểu ngữ cảnh, "
                "chỉ trả lời câu hỏi MỚI NHẤT):\n" + history_text
            )

        messages = [{"role": "system", "content": system_prompt}]
        messages.append(
            {
                "role": "user",
                "content": f"Context:\n\n{context}\n\nQuestion: {input.question}",
            }
        )

        llm_result, step_times["synthesize"] = await _timed(
            lambda: self._llm.chat_completion_reasoning(
                messages=messages,
                temperature=0.3,
                max_tokens=16384,
            ),
        )
        get_metrics().histogram("llm_synthesis_duration_seconds", step_times["synthesize"])
        answer = llm_result.get("content", "")

        sources = [
            {
                "id": r.content_id,
                "title": r.title,
                "score": round(r.score, 4),
                "page_title": r.metadata.get("page_title") if r.metadata else None,
                "page_slug": r.metadata.get("page_slug") if r.metadata else None,
                "source_name": r.metadata.get("source_name") if r.metadata else None,
                "published_at": r.metadata.get("published_at") if r.metadata else None,
                "event_date": r.metadata.get("event_date") if r.metadata else None,
            }
            for r in top_results[:5]
        ]

        usage = getattr(self._llm, "last_usage", None) or {}
        tokens_used = usage.get("total_tokens", 0)

        result = {
            "answer": answer,
            "sources": sources,
            "tokens_used": tokens_used,
            "cache_hit": cache_hit,
            "pipeline_steps": step_times,
        }

        # ── P3: variable TTL ───────────────────────────────────────────
        ttl = self._cache_ttl(input.question)
        cache_key = self._cache_key(input.question, source_id=input.source_id)
        result_json = json.dumps(result, default=str)
        _, step_times["cache_save"] = await _timed(
            lambda: self._cache.set(cache_key, result_json, ttl=ttl),
        )
        # Also store embedding for semantic cache (P2)
        _, step_times["semantic_cache_save"] = await _timed(
            lambda: self._cache.semantic_set(
                cache_key, query_embedding.vector, result_json, ttl=ttl
            ),
        )

        inc_counter("query_total", {"status": "success", "cache": "miss"})
        if telemetry and root_span:
            await telemetry.end_span(
                span=root_span,
                outputs={
                    "answer_length": len(answer),
                    "answer": answer,
                    "sources_count": len(sources),
                },
                metadata={
                    "cache_hit": False,
                    "cache_ttl": ttl,
                    "intent": intent,
                    "tokens_used": tokens_used,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "retrieved_sources_count": len(top_results),
                    "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                },
            )

        return result

    async def execute_stream(self, input: QueryInput):
        step_times: dict[str, float] = {}
        intent = "general"
        root_span: TelemetrySpan | None = None
        telemetry = self._telemetry

        yield {"type": "status", "data": {"status": "processing"}}

        if telemetry:
            root_span = await telemetry.start_span(
                name="rag_query_stream",
                kind="chain",
                inputs={
                    "question": input.question,
                    "source_id": input.source_id,
                    "top_k": input.top_k,
                    "from_date": input.from_date.isoformat() if input.from_date else None,
                    "to_date": input.to_date.isoformat() if input.to_date else None,
                },
            )
            _set_parent_on_wrappers(
                self._embedder,
                self._vector_search,
                self._keyword_search,
                self._llm,
                self._cache,
                root_span,
                rewriter=self._rewriter,
                analyzer=self._analyzer,
                event_search=self._event_search,
            )

        # ── P0: exact-match cache for stream endpoint ───────────────────
        cached_data, step_times["cache_check"] = await _timed(
            lambda: self._try_exact_cache(input.question, source_id=input.source_id)
        )
        if cached_data:
            yield self._build_cached_stream_event(cached_data)
            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span,
                    outputs={
                        "answer_length": len(cached_data.get("answer", "")),
                        "answer": cached_data.get("answer", ""),
                    },
                    metadata={
                        "cache_hit": True,
                        "cache_type": "exact",
                        "question": input.question,
                        "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                    },
                )
            return

        try:
            # ── Multi-turn: resolve follow-up pronouns via chat history ─
            question = input.question
            if self._rewriter and input.chat_history:
                question = await self._rewriter.rewrite(input.question, input.chat_history)
                logger.debug("Stream rewritten follow-up: %r → %r", input.question[:80], question[:80])

            # ── Guardrail + Intent analysis (single LLM call) ──────────
            analysis = GuardrailAnalysis()
            if self._analyzer:
                analysis, step_times["analyze"] = await _timed(
                    lambda: self._analyzer.analyze(question)
                )

                # Guardrail rejection
                if not analysis.allowed:
                    reject_answer = (
                        analysis.reason
                        or "Xin lỗi, tôi chỉ có thể trả lời các câu hỏi về "
                        "kinh tế, tài chính, chứng khoán, và đầu tư."
                    )
                    yield {
                        "type": "complete",
                        "data": {
                            "answer": reject_answer,
                            "citations": [],
                            "sources_used": [],
                            "tokens_used": 0,
                            "cache_hit": False,
                            "rejected": True,
                        },
                    }
                    inc_counter("query_total", {"status": "rejected", "cache": "n/a"})
                    if telemetry and root_span:
                        await telemetry.end_span(
                            span=root_span,
                            outputs={
                                "answer_length": len(reject_answer),
                                "answer": reject_answer,
                            },
                            metadata={
                                "allowed": False,
                                "reason": analysis.reason,
                                "question": input.question,
                                "total_latency_ms": round(
                                    sum(step_times.values()) * 1000, 2
                                ),
                            },
                        )
                    return

                intent = analysis.intent
                logger.debug(
                    "Stream guardrail: allowed=%s intent=%s time_range=%s emb_text_len=%d",
                    analysis.allowed,
                    intent,
                    analysis.time_range.start if analysis.time_range else None,
                    len(analysis.embedding_text),
                )

            # Embedding with analyzer-optimised text
            embed_text = analysis.embedding_text or question
            query_embedding, step_times["embed"] = await _timed(
                lambda: self._embedder.embed(embed_text),
            )

            # ── P2: semantic cache for stream endpoint ──────────────────
            sem_data, step_times["semantic_cache_check"] = await _timed(
                lambda: self._try_semantic_cache(query_embedding)
            )
            if sem_data:
                yield self._build_cached_stream_event(sem_data)
                if telemetry and root_span:
                    await telemetry.end_span(
                        span=root_span,
                        outputs={
                            "answer_length": len(sem_data.get("answer", "")),
                            "answer": sem_data.get("answer", ""),
                        },
                        metadata={
                            "cache_hit": True,
                            "cache_type": "semantic",
                            "question": input.question,
                            "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                        },
                    )
                return

            # ── Resolve time_range (analyzer → regex → LLM fallback) ─────
            time_range = analysis.time_range
            if time_range is None:
                if input.from_date or input.to_date:
                    min_dt = datetime.min.replace(tzinfo=get_system_tz())
                    time_range = TimeRange(start=input.from_date or min_dt, end=input.to_date)
                else:
                    time_range = _extract_time_range(question)
                    if time_range is None and _may_be_time_related(question):
                        time_range = await self._extract_time_range_with_llm(question)
            if time_range:
                logger.debug("Extracted time_range: %s → %s", time_range.start, time_range.end)

            # ── Multi-retrieval (parallel) + weighted RRF + diversity ───
            _, top_results, search_step_times = await self._retrieve_and_merge(
                input,
                query_embedding,
                time_range,
                intent=intent,
                rewritten_question=question,
                entities=analysis.entities if analysis.entities else None,
                analysis=analysis,
            )
            step_times.update(search_step_times)

            yield {"type": "status", "data": {"status": "retrieving"}}

            context = self._build_context(top_results)

            lang_stream = analysis.language or _detect_language(input.question)
            today_str_stream = now().strftime("%Y-%m-%d")
            system_prompt = _build_synthesis_prompt(lang_stream, today_str_stream, intent)
            if input.chat_history:
                turns = input.chat_history[-6:]
                history_text = "\n".join(
                    f"{'User' if t.get('role') == 'user' else 'Assistant'}: {t.get('content', '')}"
                    for t in turns
                )
                system_prompt += (
                    "\n\nCuộc hội thoại trước đó (tham khảo để hiểu ngữ cảnh, "
                    "chỉ trả lời câu hỏi MỚI NHẤT):\n" + history_text
                )

            stream_messages = [{"role": "system", "content": system_prompt}]
            stream_messages.append(
                {
                    "role": "user",
                    "content": f"Context:\n\n{context}\n\nQuestion: {input.question}",
                }
            )

            yield {"type": "status", "data": {"status": "thinking"}}

            t0_synth = time.time()
            root_logger.warning(
                "[STREAM] calling chat_completion_stream for: %s...", input.question[:50]
            )
            full_answer = ""
            async for token in self._llm.chat_completion_stream(
                messages=stream_messages,
                temperature=0.3,
                max_tokens=16384,
            ):
                full_answer += token
                yield {"type": "token", "data": token}
            step_times["synthesize"] = time.time() - t0_synth

            # Fallback: when the stream yields zero tokens (e.g. reasoning
            # model emits everything in reasoning_content deltas), fall
            # back to a non-streaming call so the user still gets an answer.
            if not full_answer:
                root_logger.warning("[STREAM] zero tokens yielded, falling back to non-streaming")
                try:
                    full_answer = await self._llm.chat_completion(
                        messages=stream_messages,
                        temperature=0.3,
                        max_tokens=16384,
                    )
                    # Chunk the fallback result so the UI receives tokens
                    for i in range(0, len(full_answer), 30):
                        yield {"type": "token", "data": full_answer[i : i + 30]}
                except Exception:
                    full_answer = "Xin lỗi, không thể tạo câu trả lời. Vui lòng thử lại."
                    for i in range(0, len(full_answer), 30):
                        yield {"type": "token", "data": full_answer[i : i + 30]}
            root_logger.warning("[STREAM] answer_len=%d", len(full_answer))

            yield {"type": "status", "data": {"status": "summarizing"}}

            usage = getattr(self._llm, "last_usage", None) or {}
            tokens_used = usage.get("total_tokens", 0)

            sources = [
                {
                    "id": r.content_id,
                    "title": r.title,
                    "score": round(r.score, 4),
                    "page_title": r.metadata.get("page_title") if r.metadata else None,
                    "page_slug": r.metadata.get("page_slug") if r.metadata else None,
                    "source_name": r.metadata.get("source_name") if r.metadata else None,
                    "published_at": r.metadata.get("published_at") if r.metadata else None,
                    "event_date": r.metadata.get("event_date") if r.metadata else None,
                }
                for r in top_results[:5]
            ]

            # ── P3: save to both cache layers with variable TTL ──────────
            cache_key = self._cache_key(input.question, source_id=input.source_id)
            ttl = self._cache_ttl(input.question)
            cache_value = json.dumps(
                {
                    "answer": full_answer,
                    "sources": sources,
                    "tokens_used": tokens_used,
                    "cache_hit": False,
                },
                default=str,
            )
            await self._cache.set(cache_key, cache_value, ttl=ttl)
            await self._cache.semantic_set(cache_key, query_embedding.vector, cache_value, ttl=ttl)

            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span,
                    outputs={
                        "answer_length": len(full_answer),
                        "answer": full_answer,
                        "sources_count": len(sources),
                    },
                    metadata={
                        "cache_ttl": ttl,
                        "intent": intent,
                        "tokens_used": tokens_used,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "retrieved_sources_count": len(top_results),
                        "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                    },
                )

            yield {
                "type": "complete",
                "data": {
                    "answer": full_answer,
                    "citations": sources,
                    "sources_used": [],
                    "tokens_used": tokens_used,
                },
            }
        except Exception as exc:
            inc_counter("query_total", {"status": "error", "cache": "n/a"})
            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span,
                    error=str(exc),
                    metadata={"error_type": type(exc).__name__},
                )
            # Always yield a "complete" event so the frontend stops the
            # loading indicator and shows the error to the user.
            error_answer = f"Xin lỗi, đã xảy ra lỗi khi xử lý câu hỏi: {exc}"
            yield {
                "type": "complete",
                "data": {
                    "answer": error_answer,
                    "citations": [],
                    "sources_used": [],
                    "tokens_used": 0,
                },
            }
