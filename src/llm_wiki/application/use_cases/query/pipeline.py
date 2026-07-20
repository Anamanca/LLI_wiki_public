import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from llm_wiki.application.dto.query_dto import QueryInput
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

root_logger = logging.getLogger()
logger = logging.getLogger(__name__)

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
    # Vietnamese — compound time phrases (high confidence)
    (r"trong\s+tháng\s+vừa\s+qua|trong\s+tháng\s+qua", lambda m: timedelta(days=30)),
    (r"tháng\s+vừa\s+qua", lambda m: timedelta(days=30)),
    (r"tuần\s+vừa\s+qua", lambda m: timedelta(days=7)),
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
    "jan": 1, "january": 1, "feb": 2, "february": 2,
    "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
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
    now = datetime.now(UTC).replace(tzinfo=None)
    if year < 2000 or year > now.year:
        return None
    start = datetime(year, 1, 1)
    return timedelta(days=(now - start).days + 1)


_TIME_KEYWORDS = re.compile(
    r"when|recent|past|last|ago|today|yesterday|week|month|year|"
    r"khi|nào|lúc|gần|đây|hôm|nay|qua|tuần|tháng|năm|trước|vừa|mới|dạo|"
    r"202[0-9]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec",
    re.IGNORECASE,
)


def _extract_time_range(question: str) -> TimeRange | None:
    now = datetime.now(UTC).replace(tzinfo=None)
    for pattern, delta_fn in _TIME_PATTERNS:
        m = re.search(pattern, question, re.IGNORECASE)
        if not m:
            continue
        try:
            delta = delta_fn(m)
            if delta is not None:
                return TimeRange(start=now - delta, end=now)
        except Exception:
            continue
    return None


def _may_be_time_related(question: str) -> bool:
    return bool(_TIME_KEYWORDS.search(question))


def _set_parent_on_wrappers(
    embedder: EmbeddingServicePort,
    vector_search: VectorSearchPort,
    keyword_search: KeywordSearchPort,
    llm: LLMClientPort,
    cache: CacheServicePort,
    parent: TelemetrySpan,
) -> None:
    """Wire the pipeline root span as parent for all traced wrappers.

    Each wrapper that exposes ``set_parent_span()`` will attach its own spans
    under *parent*, building a single tree on LangSmith.
    """
    for wrapped in (embedder, vector_search, keyword_search, llm, cache):
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
    ):
        self._embedder = embedder
        self._vector_search = vector_search
        self._keyword_search = keyword_search
        self._llm = llm
        self._cache = cache
        self._telemetry = telemetry

    def _cache_key(self, question: str) -> str:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        return f"qa:{hashlib.sha256((question + today).encode()).hexdigest()}"

    async def _extract_time_range_with_llm(self, question: str) -> TimeRange | None:
        now = datetime.now(UTC).replace(tzinfo=None)
        prompt = (
            'Extract the time range from this question. '
            'Return ONLY a JSON object: {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"} '
            'or {"start_date": null, "end_date": null} if no time range is implied. '
            'Use "now" as end_date for relative times like "past month" or "recent". '
            f'Today is {now.strftime("%Y-%m-%d")}. '
            f'Question: {question}'
        )
        try:
            raw = await self._llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=128,
            )
            data = json.loads(raw.strip())
            if data.get("start_date"):
                start = datetime.fromisoformat(data["start_date"])
                end_str = data.get("end_date", "now")
                end = now if end_str == "now" else datetime.fromisoformat(end_str)
                return TimeRange(start=start, end=end)
        except Exception:
            logger.debug("LLM time extraction failed")
        return None

    def _resolve_time_range(self, input: QueryInput) -> TimeRange | None:
        if input.from_date or input.to_date:
            return TimeRange(start=input.from_date or datetime.min, end=input.to_date)
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
        context_parts = []
        for i, result in enumerate(results[:20], start=1):
            source = result.metadata.get("source_name", "unknown") if result.metadata else "unknown"
            context_parts.append(f"[{i}] ({source}) {result.title}\n{result.content[:800]}")
        return "\n\n".join(context_parts)

    async def _retrieve_and_merge(
        self,
        input: QueryInput,
        query_embedding: Embedding,
        time_range: TimeRange | None,
    ) -> tuple[list[SearchResult], list[SearchResult], list[SearchResult], dict[str, float]]:
        """Run vector + keyword search and reciprocal rank fusion.

        Tracing is emitted by the traced wrappers (TracedVectorSearchWrapper,
        TracedKeywordSearchWrapper) — pipeline only measures wall-clock time.
        """
        step_times: dict[str, float] = {}

        vector_results, step_times["vector_search"] = await _timed(
            lambda: self._vector_search.search_similar(
                query_embedding,
                top_k=input.top_k * 2,
                source_id=input.source_id,
                time_range=time_range,
            ),
        )

        keyword_results, step_times["keyword_search"] = await _timed(
            lambda: self._keyword_search.search_keyword(
                input.question,
                top_k=input.top_k,
                time_range=time_range,
            ),
        )

        def _merge():
            merged = self._reciprocal_rank_fusion([vector_results, keyword_results])
            return merged[: input.top_k]

        top_results, step_times["merge"] = await _timed(_merge)

        return vector_results, keyword_results, top_results, step_times

    async def execute(self, input: QueryInput) -> dict:
        step_times: dict[str, float] = {}
        cache_hit = False
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
                self._embedder, self._vector_search, self._keyword_search,
                self._llm, self._cache, root_span,
            )

        cache_key = self._cache_key(input.question)
        cached, step_times["cache_check"] = await _timed(lambda: self._cache.get(cache_key))

        if cached:
            cache_hit = True
            data = json.loads(cached)
            data["cache_hit"] = True
            data["pipeline_steps"] = step_times
            if telemetry and root_span:
                await telemetry.add_metadata(
                    span=root_span,
                    metadata={
                        "cache_hit": True,
                        "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                    },
                )
                await telemetry.end_span(
                    span=root_span,
                    outputs={"answer_length": len(data.get("answer", ""))},
                )
            return data

        try:
            query_embedding, step_times["embed"] = await _timed(
                lambda: self._embedder.embed(input.question),
            )
        except Exception as exc:
            if telemetry and root_span:
                await telemetry.add_metadata(
                    span=root_span,
                    metadata={"error_type": type(exc).__name__},
                )
                await telemetry.end_span(span=root_span, error=str(exc))
            raise

        time_range = self._resolve_time_range(input)
        if time_range:
            logger.debug("Extracted time_range: %s → %s", time_range.start, time_range.end)

        _, _, top_results, search_step_times = await self._retrieve_and_merge(
            input, query_embedding, time_range
        )
        step_times.update(search_step_times)

        context = self._build_context(top_results)

        system_prompt = (
            "You are an expert assistant. Answer the question based ONLY on the provided context. "
            "Cite sources using [N] notation. If the context doesn't contain the answer, say so."
        )

        answer, step_times["synthesize"] = await _timed(
            lambda: self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"Context:\n\n{context}\n\nQuestion: {input.question}",
                    },
                ],
                temperature=0.3,
                max_tokens=4096,
            ),
        )

        sources = [
            {
                "id": r.content_id,
                "title": r.title,
                "score": round(r.score, 4),
                "page_title": r.metadata.get("page_title") if r.metadata else None,
                "page_slug": r.metadata.get("page_slug") if r.metadata else None,
                "source_name": r.metadata.get("source_name") if r.metadata else None,
                "published_at": r.metadata.get("published_at") if r.metadata else None,
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

        _, step_times["cache_save"] = await _timed(
            lambda: self._cache.set(cache_key, json.dumps(result, default=str), ttl=3600),
        )

        if telemetry and root_span:
            await telemetry.add_metadata(
                span=root_span,
                metadata={
                    "cache_hit": False,
                    "tokens_used": tokens_used,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "retrieved_sources_count": len(top_results),
                    "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                },
            )
            await telemetry.end_span(
                span=root_span,
                outputs={
                    "answer_length": len(answer),
                    "sources_count": len(sources),
                },
            )

        return result

    async def execute_stream(self, input: QueryInput):
        step_times: dict[str, float] = {}
        root_span: TelemetrySpan | None = None
        telemetry = self._telemetry

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
                self._embedder, self._vector_search, self._keyword_search,
                self._llm, self._cache, root_span,
            )

        query_embedding, step_times["embed"] = await _timed(
            lambda: self._embedder.embed(input.question),
        )

        async def _resolve_time():
            return await self._resolve_time_range_async(input)

        time_range, step_times["time_resolution"] = await _timed(_resolve_time)
        if time_range:
            logger.debug("Extracted time_range: %s → %s", time_range.start, time_range.end)

        _, _, top_results, search_step_times = await self._retrieve_and_merge(
            input, query_embedding, time_range
        )
        step_times.update(search_step_times)

        context = self._build_context(top_results)

        system_prompt = (
            "You are an expert assistant. Answer the question based ONLY on the provided context. "
            "Cite sources using [N] notation. If the context doesn't contain the answer, say so."
        )

        trace_id = root_span.span_id if root_span else None
        yield {"type": "metadata", "data": {"pipeline_steps": step_times, "trace_id": trace_id}}

        full_answer = ""
        has_content = False
        t0_synth = time.time()

        # Trace emitted by TracedLLMWrapper.chat_completion_stream
        # (parent was wired via _set_parent_on_wrappers above).
        root_logger.warning(
            "[STREAM] calling chat_completion_stream for: %s...", input.question[:50]
        )
        try:
            async for chunk in self._llm.chat_completion_stream(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"Context:\n\n{context}\n\nQuestion: {input.question}",
                    },
                ],
                temperature=0.3,
                max_tokens=4096,
            ):
                has_content = True
                full_answer += chunk
                yield {"type": "chunk", "data": chunk}
        except Exception as exc:
            if telemetry and root_span:
                await telemetry.add_metadata(
                    span=root_span,
                    metadata={"error_type": type(exc).__name__},
                )
                await telemetry.end_span(span=root_span, error=str(exc))
            raise

        step_times["synthesize"] = time.time() - t0_synth
        if not has_content:
            root_logger.warning("[STREAM] falling back to non-stream")
            full_answer = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (f"Context:\n\n{context}\n\nQuestion: {input.question}"),
                    },
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            root_logger.warning("[STREAM] fallback answer_len=%d", len(full_answer))
            yield {"type": "chunk", "data": full_answer}

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
            }
            for r in top_results[:5]
        ]

        if telemetry and root_span:
            await telemetry.add_metadata(
                span=root_span,
                metadata={
                    "tokens_used": tokens_used,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "retrieved_sources_count": len(top_results),
                    "total_latency_ms": round(sum(step_times.values()) * 1000, 2),
                },
            )
            await telemetry.end_span(
                span=root_span,
                outputs={
                    "answer_length": len(full_answer),
                    "sources_count": len(sources),
                },
            )

        yield {"type": "sources", "data": sources}
        yield {"type": "done", "data": None}
