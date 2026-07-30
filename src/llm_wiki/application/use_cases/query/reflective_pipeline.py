"""Self-reflective RAG pipeline — retrieve → generate → evaluate → retry.

This is the Phase 3 upgrade from naive RAG to Agentic RAG. Instead of
running retrieve→generate once and returning, the pipeline evaluates
its own output and retries with different strategies until the answer
meets quality thresholds or the attempt budget is exhausted.

Architecture:
    The reflective pipeline wraps the existing ``QueryPipeline`` and adds
    an evaluation+retry loop. It does NOT change the core retrieval or
    generation logic — it only adds the meta-cognitive layer on top.

Strategies attempted in order:
    1. default   — standard retrieval with original/rewritten query
    2. hyde      — hypothetical document embedding for vector search
    3. decompose — break complex question into sub-queries
    4. expand    — add synonyms to keyword search query
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime

from llm_wiki.shared.datetime_utils import now
from typing import Any

from llm_wiki.application.dto.query_dto import QueryInput
from llm_wiki.application.ports.search.answer_evaluator_port import (
    AnswerEvaluation,
    AnswerEvaluatorPort,
)
from llm_wiki.application.ports.search.graph_rag_port import GraphRAGPort
from llm_wiki.application.ports.search.query_analyzer_port import (
    QueryAnalysis,
    QueryAnalyzerPort,
)
from llm_wiki.application.ports.search.query_expander_port import QueryExpanderPort
from llm_wiki.application.ports.search.query_rewriter_port import QueryRewriterPort
from llm_wiki.application.ports.search.reranker_port import RerankerPort
from llm_wiki.application.ports.search.vector_search import (
    CacheServicePort,
    EmbeddingServicePort,
    KeywordSearchPort,
    LLMClientPort,
    VectorSearchPort,
)
from llm_wiki.application.ports.search.event_search_port import EventSearchPort
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange
from llm_wiki.infrastructure.telemetry.business_metrics import inc_counter

logger = logging.getLogger(__name__)

# ── Stopping thresholds ────────────────────────────────────────────────

_MIN_FAITHFULNESS = 7.0       # Must be grounded in context
_MIN_COMPLETENESS = 7.0       # Must address the question adequately
_MAX_ATTEMPTS = 3             # Hard budget — never loop more than this

# ── Source diversity threshold ─────────────────────────────────────────
_MIN_DISTINCT_SOURCES = 2     # Warn if all top results from < this many pages

# ── HyDE prompt ────────────────────────────────────────────────────────

_HYDE_SYSTEM_PROMPT = (
    "Viết một đoạn văn ngắn gọn trả lời câu hỏi sau. "
    "KHÔNG CẦN chính xác — chỉ cần viết có vẻ hợp lý và chứa các từ khóa "
    "có thể xuất hiện trong tài liệu thực tế. Viết bằng tiếng Việt. "
    "Đoạn văn (tối đa 200 từ):"
)

# ── Decomposition prompt ───────────────────────────────────────────────

_DECOMPOSE_SYSTEM_PROMPT = (
    "Phân rã câu hỏi phức tạp thành 2-4 câu hỏi con ĐƠN GIẢN, ĐỘC LẬP. "
    "Mỗi câu hỏi con phải có thể trả lời độc lập. "
    "Output JSON: {\"sub_queries\": [\"...\", \"...\"]}. "
    "CHỈ output JSON, không markdown."
)

# ── Adaptive system prompt fragments per intent ────────────────────────

_INTENT_SYSTEM_PROMPTS: dict[str, str] = {
    "timeline": (
        "Bạn là chuyên gia phân tích diễn biến theo thời gian. "
        "Hãy trả lời câu hỏi dựa TRÊN NGỮ CẢNH được cung cấp. "
        "SẮP XẾP câu trả lời THEO TRÌNH TỰ THỜI GIAN. "
        "Mỗi mốc sự kiện PHẢI có ngày tháng năm cụ thể. "
        "Dùng định dạng: **Ngày tháng năm:** Sự kiện → Diễn biến → Hệ quả.\n\n"
    ),
    "comparative": (
        "Bạn là chuyên gia phân tích SO SÁNH. "
        "Hãy trả lời câu hỏi dựa TRÊN NGỮ CẢNH được cung cấp. "
        "Trình bày DƯỚI DẠNG SO SÁNH ĐỐI CHIẾU rõ ràng. "
        "Nếu có thể, dùng cấu trúc: "
        "(1) Điểm giống nhau, (2) Điểm khác nhau, (3) Ưu/nhược điểm từng bên. "
        "Ghi rõ THỜI ĐIỂM của từng dữ liệu được so sánh.\n\n"
    ),
    "current_state": (
        "Bạn là chuyên gia cập nhật tình hình HIỆN TẠI. "
        "Hãy trả lời câu hỏi dựa TRÊN NGỮ CẢNH được cung cấp. "
        "ƯU TIÊN thông tin MỚI NHẤT. "
        "Ghi rõ NGÀY THÁNG cho mọi thông tin. "
        "Nếu có số liệu, trích dẫn con số CỤ THỂ.\n\n"
    ),
    "historical": (
        "Bạn là chuyên gia phân tích SỰ KIỆN LỊCH SỬ. "
        "Hãy trả lời câu hỏi dựa TRÊN NGỮ CẢNH được cung cấp. "
        "GHI RÕ NGÀY THÁNG NĂM cụ thể cho mọi thông tin. "
        "Phân tích NGUYÊN NHÂN → DIỄN BIẾN → HỆ QUẢ.\n\n"
    ),
    "general": (
        "Bạn là trợ lý nghiên cứu chuyên sâu. "
        "Hãy trả lời câu hỏi dựa TRÊN NGỮ CẢNH được cung cấp. "
        "Câu trả lời phải bao gồm: "
        "(1) một câu tóm tắt ngắn gọn ở đầu; "
        "(2) phân tích chi tiết từng khía cạnh liên quan, "
        "kèm ví dụ cụ thể từ ngữ cảnh; "
        "(3) giải thích mối liên hệ giữa các ý; "
        "(4) kết luận tổng thể ở cuối.\n\n"
    ),
}


class SelfReflectiveRAGPipeline:
    """RAG pipeline with self-evaluation and strategy-based retry loops.

    Wraps the existing ``QueryPipeline`` and adds:
    - Answer evaluation via LLM-as-judge
    - Strategy switching: default → hyde → decompose → expand
    - Hard attempt budget to prevent infinite loops
    - GraphRAG integration for entity-aware questions
    - Adaptive system prompts per intent

    Usage::

        reflective = SelfReflectiveRAGPipeline(
            base_pipeline=pipeline,
            evaluator=evaluator,
            expander=expander,
            re_ranker=re_ranker,
            graph_rag=graph_rag,
            llm=llm,
            embedder=embedder,
            cache=cache,
            telemetry=telemetry,
        )
        result = await reflective.execute(input)
    """

    def __init__(
        self,
        base_pipeline: QueryPipeline,
        evaluator: AnswerEvaluatorPort,
        expander: QueryExpanderPort | None = None,
        re_ranker: RerankerPort | None = None,
        graph_rag: GraphRAGPort | None = None,
        llm: LLMClientPort | None = None,
        embedder: EmbeddingServicePort | None = None,
        vector_search: VectorSearchPort | None = None,
        keyword_search: KeywordSearchPort | None = None,
        event_search: EventSearchPort | None = None,
        cache: CacheServicePort | None = None,
        telemetry: TelemetryPort | None = None,
        rewriter: QueryRewriterPort | None = None,
        analyzer: QueryAnalyzerPort | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
    ):
        self._pipeline = base_pipeline
        self._evaluator = evaluator
        self._expander = expander
        self._re_ranker = re_ranker
        self._graph_rag = graph_rag
        self._llm = llm
        self._embedder = embedder
        self._vector_search = vector_search
        self._keyword_search = keyword_search
        self._event_search = event_search
        self._cache = cache
        self._telemetry = telemetry
        self._rewriter = rewriter
        self._analyzer = analyzer
        self._max_attempts = max_attempts

        # Store unwrapped adapters for recency-lambda re-creation after intent detection.
        # Traced wrappers wrap inner adapters; we extract the raw adapter so we can
        # reconstruct with a different recency_lambda.
        self._raw_vector_search: VectorSearchPort | None = self._unwrap_traced(vector_search)
        self._raw_keyword_search: KeywordSearchPort | None = self._unwrap_traced(keyword_search)
        self._raw_event_search: EventSearchPort | None = self._unwrap_traced(event_search)

    # ── Traced-wrapper helpers ───────────────────────────────────────────

    @staticmethod
    def _unwrap_traced(wrapped) -> Any | None:
        """Extract the raw adapter from a traced wrapper (if wrapped)."""
        if wrapped is None:
            return None
        # Traced*Wrapper classes store the real adapter in ``_inner``.
        inner = getattr(wrapped, "_inner", None)
        return inner if inner is not None else wrapped

    def _rebuild_search_adapters(self, intent: str) -> None:
        """Recreate vector/keyword/event search adapters with intent-specific recency_lambda.

        Must be called after intent is known. Re-wraps into new traced wrappers
        and wires them into the current root span so LangSmith traces remain
        a single tree instead of splitting into isolated runs.
        """
        from llm_wiki.application.use_cases.query.pipeline import recency_decay_for_intent

        new_lambda = recency_decay_for_intent(intent)
        # The root span is stored on the pipeline instance by execute()/execute_stream()
        root = getattr(self, "_root_span", None)

        if self._raw_vector_search is not None:
            rebuilt = self._raw_vector_search.__class__(
                self._raw_vector_search._session,
                recency_lambda=new_lambda,
            )
            from llm_wiki.infrastructure.search.traced_search_wrapper import TracedVectorSearchWrapper
            wrapper = TracedVectorSearchWrapper(rebuilt, self._telemetry)
            if root:
                wrapper.set_parent_span(root)
            self._vector_search = wrapper

        if self._raw_keyword_search is not None:
            rebuilt = self._raw_keyword_search.__class__(
                self._raw_keyword_search._session,
                recency_lambda=new_lambda,
            )
            from llm_wiki.infrastructure.search.traced_search_wrapper import TracedKeywordSearchWrapper
            wrapper = TracedKeywordSearchWrapper(rebuilt, self._telemetry)
            if root:
                wrapper.set_parent_span(root)
            self._keyword_search = wrapper

        if self._raw_event_search is not None:
            rebuilt = self._raw_event_search.__class__(
                self._raw_event_search._session,
                recency_lambda=new_lambda,
            )
            from llm_wiki.infrastructure.search.traced_event_search_wrapper import TracedEventSearchWrapper
            wrapper = TracedEventSearchWrapper(rebuilt, self._telemetry)
            if root:
                wrapper.set_parent_span(root)
            self._event_search = wrapper

        logger.debug("Rebuilt search adapters with recency_lambda=%.3f for intent=%r", new_lambda, intent)

    # ── Cache helpers (delegates to base pipeline) ───────────────────────

    def _cache_key(self, question: str, source_id: str | None = None) -> str:
        """Forward to base pipeline's cache key logic."""
        return self._pipeline._cache_key(question, source_id=source_id)

    def _cache_ttl(self, question: str) -> int:
        """Forward to base pipeline's TTL logic."""
        return self._pipeline._cache_ttl(question)

    async def _try_exact_cache(self, question: str, source_id: str | None = None) -> dict | None:
        """Check exact-match cache. Returns parsed cached data or None on miss."""
        if not self._cache:
            return None
        cache_key = self._cache_key(question, source_id=source_id)
        cached = await self._cache.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                logger.debug("Exact cache hit but JSON decode failed, ignoring")
        return None

    async def _try_semantic_cache(self, embedding: Any) -> dict | None:
        """Check semantic (embedding similarity) cache."""
        if not self._cache:
            return None
        try:
            from llm_wiki.application.use_cases.query.pipeline import _SEMANTIC_THRESHOLD
            vector = embedding.vector if hasattr(embedding, "vector") else embedding
            raw = await self._cache.semantic_get(vector, _SEMANTIC_THRESHOLD)
            if raw:
                return json.loads(raw)
        except Exception:
            logger.debug("Semantic cache lookup failed, ignoring", exc_info=True)
        return None

    async def _save_cache(self, question: str, source_id: str | None, answer: str, sources: list[dict],
                          embedding: Any, tokens_used: int, attempts: int) -> None:
        """Save result to both exact and semantic cache."""
        if not self._cache:
            return
        try:
            ttl = self._cache_ttl(question)
            cache_key = self._cache_key(question, source_id=source_id)
            result = {
                "answer": answer,
                "sources": sources,
                "tokens_used": tokens_used,
                "cache_hit": False,
                "pipeline_steps": {"attempts": attempts, "stop_reason": "quality_ok"},
            }
            result_json = json.dumps(result, default=str)
            await self._cache.set(cache_key, result_json, ttl=ttl)
            if hasattr(embedding, "vector"):
                await self._cache.semantic_set(cache_key, embedding.vector, result_json, ttl=ttl)
            logger.debug("Reflective: saved to cache key=%s ttl=%d", cache_key[:32], ttl)
        except Exception:
            logger.debug("Cache save failed, ignoring", exc_info=True)

    # ── Public API ──────────────────────────────────────────────────────

    async def execute(self, input: QueryInput) -> dict:
        """Run the reflective RAG pipeline and return the best answer found."""
        root_span: TelemetrySpan | None = None
        telemetry = self._telemetry
        t0 = time.time()

        if telemetry:
            root_span = await telemetry.start_span(
                name="reflective_rag",
                kind="chain",
                inputs={"question": input.question, "max_attempts": self._max_attempts},
            )
            self._root_span = root_span
            # Wire all traced wrappers under the root span so LangSmith
            # shows a single trace tree instead of isolated runs.
            from llm_wiki.application.use_cases.query.pipeline import _set_parent_on_wrappers
            _set_parent_on_wrappers(
                self._embedder, self._vector_search, self._keyword_search,
                self._llm, self._cache, root_span,
                rewriter=self._rewriter,
                analyzer=self._analyzer,
                event_search=self._event_search,
            )

        state: dict[str, Any] = {
            "question": input.question,
            "rewritten_question": input.question,
            "intent": "general",
            "attempt": 0,
            "strategy": "default",
            "context": "",
            "top_results": [],
            "answer": "",
            "evaluation": None,
            "sources": [],
            "analysis": QueryAnalysis(),
            "time_range": None,
            "embedding": None,
            "hyde_embedding": None,
            "sub_results": [],
            "stop_reason": "max_attempts",
            "prev_content_ids": [],   # track for re-rank skipping
            "prev_context": "",       # track for eval skipping
        }

        try:
            # ── Cache check (before any LLM/DB work) ─────────────────────
            # Check exact-match cache first
            exact_cached = await self._try_exact_cache(
                input.question, source_id=input.source_id,
            )
            if exact_cached:
                inc_counter("query_total", {"status": "success", "cache": "exact"})
                exact_cached["cache_hit"] = True
                if telemetry and root_span:
                    await telemetry.end_span(
                        span=root_span,
                        outputs={
                            "answer_length": len(exact_cached.get("answer", "")),
                            "answer": exact_cached.get("answer", ""),
                        },
                        metadata={"cache_hit": True, "cache_type": "exact"},
                    )
                return exact_cached

            # ── Pre-processing (shared across attempts) ─────────────────
            state = await self._pre_process(input, state)

            # ── Adaptive recency decay: rebuild adapters for intent ─────
            self._rebuild_search_adapters(state["intent"])

            # ── Semantic cache check (after we have the embedding) ───────
            if state.get("embedding"):
                sem_cached = await self._try_semantic_cache(state["embedding"])
                if sem_cached:
                    inc_counter("query_total", {"status": "success", "cache": "semantic"})
                    sem_cached["cache_hit"] = True
                    if telemetry and root_span:
                        await telemetry.end_span(
                            span=root_span,
                            outputs={
                                "answer_length": len(sem_cached.get("answer", "")),
                                "answer": sem_cached.get("answer", ""),
                            },
                            metadata={"cache_hit": True, "cache_type": "semantic"},
                        )
                    return sem_cached

            # ── Reflection loop ─────────────────────────────────────────
            while state["attempt"] < self._max_attempts:
                state["attempt"] += 1
                logger.info(
                    "Reflective RAG attempt %d/%d, strategy=%s",
                    state["attempt"], self._max_attempts, state["strategy"],
                )

                # Retrieve (with strategy-specific behavior)
                state = await self._retrieve_with_strategy(input, state)

                # Smart re-rank: skip if top results unchanged from prior attempt
                current_content_ids = [r.content_id for r in state["top_results"][:5]]
                results_changed = current_content_ids != state.get("prev_content_ids", [])
                state["prev_content_ids"] = current_content_ids

                if self._re_ranker and state["top_results"] and len(state["top_results"]) > 5:
                    if results_changed or state["attempt"] == 1:
                        state["top_results"] = await self._re_ranker.rerank(
                            state["rewritten_question"], state["top_results"], top_n=20,
                        )
                    else:
                        logger.debug("Skipping re-rank: top results unchanged from prior attempt")

                # Build context
                state["context"] = self._pipeline._build_context(state["top_results"])

                # Smart evaluate: skip LLM eval if context identical to prior attempt
                context_changed = state["context"] != state.get("prev_context", "")
                state["prev_context"] = state["context"]

                # Generate
                state = await self._generate(input, state)

                # Evaluate (skip LLM eval if context unchanged — reuse prior)
                if context_changed or state["attempt"] == 1:
                    state["evaluation"] = await self._evaluate(state)
                else:
                    logger.debug("Skipping eval: context unchanged from prior attempt, reusing")
                    if state["evaluation"] is None:
                        state["evaluation"] = await self._evaluate(state)

                # Source diversity check: if all results from ≤1 page, bias scores down
                state["evaluation"] = self._check_source_diversity(state["top_results"], state["evaluation"])

                # Decide
                if state["evaluation"].should_stop:
                    state["stop_reason"] = "quality_ok"
                    logger.info(
                        "Stopping: F=%.1f C=%.1f at attempt %d",
                        state["evaluation"].faithfulness,
                        state["evaluation"].completeness,
                        state["attempt"],
                    )
                    break

                # Pick next strategy
                next_strategy = state["evaluation"].suggested_strategy or ""
                if next_strategy == "refine_query":
                    # Use evaluator's refined_query to update the search query
                    refined = state["evaluation"].refined_query
                    if refined and refined != state["rewritten_question"]:
                        state["rewritten_question"] = refined
                        logger.debug("Refining query: %r → %r", state["question"][:80], refined[:80])
                        # Re-embed the refined query
                        if self._embedder:
                            try:
                                state["embedding"] = await self._embedder.embed(refined)
                            except Exception:
                                pass
                    # After refinement, cycle to next in order (don't stay on refine_query)
                    next_strategy = self._next_strategy(state["strategy"])
                elif next_strategy == state["strategy"]:
                    next_strategy = self._next_strategy(state["strategy"])
                state["strategy"] = next_strategy
                logger.info(
                    "Retry with strategy=%s (F=%.1f C=%.1f): %s",
                    state["strategy"],
                    state["evaluation"].faithfulness,
                    state["evaluation"].completeness,
                    state["evaluation"].missing_info[:100],
                )

            # ── Build final result ──────────────────────────────────────
            state["sources"] = self._build_sources(state["top_results"])
            total_latency = (time.time() - t0) * 1000

            self._emit_metrics(state, total_latency)

            # ── Save to cache ───────────────────────────────────────────
            await self._save_cache(
                question=input.question,
                source_id=input.source_id,
                answer=state["answer"],
                sources=state["sources"],
                embedding=state.get("embedding"),
                tokens_used=(getattr(self._llm, "last_usage", None) or {}).get("total_tokens", 0) if self._llm else 0,
                attempts=state["attempt"],
            )

            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span,
                    outputs={
                        "answer_length": len(state["answer"]),
                        "answer": state["answer"],
                        "attempts": state["attempt"],
                        "stop_reason": state["stop_reason"],
                    },
                    metadata={
                        "attempts": state["attempt"],
                        "final_strategy": state["strategy"],
                        "intent": state["intent"],
                        "stop_reason": state["stop_reason"],
                        "total_latency_ms": round(total_latency, 2),
                        "faithfulness": state["evaluation"].faithfulness if state["evaluation"] else None,
                        "completeness": state["evaluation"].completeness if state["evaluation"] else None,
                    },
                )

            return {
                "answer": state["answer"],
                "sources": state["sources"],
                "tokens_used": (getattr(self._llm, "last_usage", None) or {}).get("total_tokens", 0) if self._llm else 0,
                "cache_hit": False,
                "pipeline_steps": {"attempts": state["attempt"], "stop_reason": state["stop_reason"]},
            }

        except Exception as exc:
            inc_counter("query_total", {"status": "error", "cache": "reflective_failure"})
            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span,
                    error=str(exc),
                    metadata={"error_type": type(exc).__name__},
                )
            raise

    # ── Pre-processing (shared across attempts) ─────────────────────────

    async def _pre_process(self, input: QueryInput, state: dict) -> dict:
        """Run query rewriting and analysis once before the reflection loop."""
        # Query rewrite
        if self._rewriter and input.chat_history:
            try:
                rewritten = await self._rewriter.rewrite(input.question, input.chat_history)
                if rewritten != input.question:
                    state["rewritten_question"] = rewritten
                    logger.debug("Reflective: rewritten %r → %r", input.question[:80], rewritten[:80])
            except Exception:
                pass

        # Query analysis
        if self._analyzer:
            try:
                analysis = await self._analyzer.analyze(state["rewritten_question"])
                state["analysis"] = analysis
                state["intent"] = analysis.intent
            except Exception:
                pass

        # Time range
        state["time_range"] = self._pipeline._resolve_time_range(input)
        if not state["time_range"] and state["analysis"].time_range:
            state["time_range"] = state["analysis"].time_range

        # Embedding (used by default and expand strategies)
        if self._embedder:
            try:
                state["embedding"] = await self._embedder.embed(state["rewritten_question"])
            except Exception:
                pass

        return state

    # ── Strategy-specific retrieval ────────────────────────────────────

    async def _retrieve_with_strategy(self, input: QueryInput, state: dict) -> dict:
        """Run retrieval with the current strategy's behavior."""
        strategy = state["strategy"]
        query_embedding = state["embedding"]
        time_range = state["time_range"]
        intent = state["intent"]

        from llm_wiki.application.use_cases.query.pipeline import _build_keyword_query

        analysis = state.get("analysis")
        kw_query = _build_keyword_query(state["rewritten_question"], analysis)

        if strategy == "hyde":
            # Reuse cached HyDE doc if available (skip re-generation)
            hyde_doc = state.get("_hyde_doc") or await self._generate_hyde(state["rewritten_question"])
            state["_hyde_doc"] = hyde_doc  # cache for potential retry
            if self._embedder:
                hyde_embedding = await self._embedder.embed(hyde_doc)
                state["hyde_embedding"] = hyde_embedding
                if self._vector_search and self._keyword_search:
                    # Build parallel tasks — same 4 streams as default + graph
                    tasks = [
                        self._vector_search.search_similar(
                            hyde_embedding, top_k=input.top_k * 2,
                            source_id=input.source_id, time_range=time_range,
                        ),
                        self._keyword_search.search_keyword(
                            kw_query, top_k=input.top_k, time_range=time_range,
                        ),
                    ]
                    stream_names = ["sections", "keyword_sections"]

                    if self._event_search:
                        tasks.append(self._event_search.search_events(
                            hyde_embedding, top_k=input.top_k * 2, time_range=time_range,
                        ))
                        stream_names.append("events")
                        tasks.append(self._event_search.search_events_keyword(
                            kw_query, top_k=input.top_k, time_range=time_range,
                        ))
                        stream_names.append("keyword_events")

                    tasks.append(self._get_graph_results(state))
                    stream_names.append("graph")

                    results_raw = await asyncio.gather(*tasks, return_exceptions=True)
                    result_sets, top_results = self._merge_streams(
                        stream_names, results_raw, intent, input.top_k,
                        time_range=time_range,
                    )
                    state["top_results"] = top_results

        elif strategy == "refine_query":
            # Handle explicitly: use refined (re-embedded) query.
            # The refined query is set in state["rewritten_question"] before entering
            # this handler; we just need to use it for retrieval.
            state["top_results"] = await self._default_retrieval(
                input, state["embedding"], time_range, intent, state,
            )

        elif strategy == "decompose":
            # Use analyzer sub_questions when available; otherwise fall back to
            # LLM decomposition (expensive — only called if reflective loop
            # decides decomposition is needed and analyzer didn't provide sub_qs).
            analysis = state.get("analysis")
            if analysis and analysis.sub_questions:
                sub_queries = analysis.sub_questions
            else:
                sub_queries = await self._decompose(state["rewritten_question"])
            if sub_queries and self._embedder:
                # Fetch GraphRAG results once (entity-based, same for all sub-queries)
                graph_results = await self._get_graph_results(state)

                all_results: list[SearchResult] = []
                seen_ids: set[str] = set()
                for sq in sub_queries[:3]:
                    try:
                        sq_embedding = await self._embedder.embed(sq)
                        if self._vector_search and self._keyword_search:
                            sq_vec, sq_kw = await asyncio.gather(
                                self._vector_search.search_similar(
                                    sq_embedding, top_k=max(5, input.top_k // 2),
                                    source_id=input.source_id, time_range=time_range,
                                ),
                                self._keyword_search.search_keyword(
                                    sq, top_k=max(5, input.top_k // 2), time_range=time_range,
                                ),
                                return_exceptions=True,
                            )
                            # Merge vector + keyword per sub-query, skipping graph (added at end)
                            _, sq_top = self._merge_streams(
                                ["sections", "keyword_sections"],
                                [sq_vec, sq_kw],
                                intent, input.top_k,
                                time_range=time_range,
                            )
                            # Deduplicate cross-sub-query by content_id
                            for r in sq_top:
                                if r.content_id not in seen_ids:
                                    seen_ids.add(r.content_id)
                                    all_results.append(r)
                    except Exception:
                        continue

                # Merge graph results at the end (deduplicated)
                if isinstance(graph_results, list) and graph_results:
                    for r in graph_results:
                        if r.content_id not in seen_ids:
                            seen_ids.add(r.content_id)
                            all_results.append(r)

                state["top_results"] = all_results[:input.top_k] if all_results else []
            else:
                # Fallback to default
                state["top_results"] = await self._default_retrieval(input, query_embedding, time_range, intent, state)

        elif strategy == "expand" and self._expander:
            # Expand query with synonyms for keyword search
            expanded_query = await self._expander.expand(
                state["rewritten_question"], intent=intent,
            )
            if self._vector_search and self._keyword_search and query_embedding:
                # Build parallel tasks — same 4 streams as default + graph
                tasks = [
                    self._vector_search.search_similar(
                        query_embedding, top_k=input.top_k * 2,
                        source_id=input.source_id, time_range=time_range,
                    ),
                    self._keyword_search.search_keyword(
                        expanded_query, top_k=input.top_k, time_range=time_range,
                    ),
                ]
                stream_names = ["sections", "keyword_sections"]

                if self._event_search:
                    tasks.append(self._event_search.search_events(
                        query_embedding, top_k=input.top_k * 2, time_range=time_range,
                    ))
                    stream_names.append("events")
                    tasks.append(self._event_search.search_events_keyword(
                        expanded_query, top_k=input.top_k, time_range=time_range,
                    ))
                    stream_names.append("keyword_events")

                tasks.append(self._get_graph_results(state))
                stream_names.append("graph")

                results_raw = await asyncio.gather(*tasks, return_exceptions=True)
                result_sets, top_results = self._merge_streams(
                    stream_names, results_raw, intent, input.top_k,
                    time_range=time_range,
                )
                state["top_results"] = top_results
            else:
                state["top_results"] = await self._default_retrieval(input, query_embedding, time_range, intent, state)

        else:
            # Default strategy — standard multi-retrieval
            state["top_results"] = await self._default_retrieval(input, query_embedding, time_range, intent, state)

        return state

    async def _default_retrieval(
        self, input: QueryInput, query_embedding, time_range, intent: str, state: dict,
    ) -> list[SearchResult]:
        """Run the standard 4-stream retrieval + optional GraphRAG."""
        if not query_embedding or not self._vector_search or not self._keyword_search:
            return []

        from llm_wiki.application.use_cases.query.pipeline import _build_keyword_query

        analysis = state.get("analysis")
        kw_query = _build_keyword_query(state["rewritten_question"], analysis)

        tasks = []
        task_names = []

        # Core streams
        tasks.append(self._vector_search.search_similar(
            query_embedding, top_k=input.top_k * 2,
            source_id=input.source_id, time_range=time_range,
        ))
        task_names.append("sections")

        tasks.append(self._keyword_search.search_keyword(
            kw_query, top_k=input.top_k, time_range=time_range,
        ))
        task_names.append("keyword_sections")

        # Event streams if available
        if self._event_search:
            tasks.append(self._event_search.search_events(
                query_embedding, top_k=input.top_k * 2, time_range=time_range,
            ))
            task_names.append("events")
            tasks.append(self._event_search.search_events_keyword(
                kw_query, top_k=input.top_k, time_range=time_range,
            ))
            task_names.append("keyword_events")

        # Graph stream if entities available
        if self._graph_rag and state["analysis"].entities:
            tasks.append(self._get_graph_results(state))
            task_names.append("graph")

        results_raw = await asyncio.gather(*tasks, return_exceptions=True)

        result_sets, top_results = self._merge_streams(
            task_names, results_raw, intent, input.top_k,
            time_range=time_range,
        )
        return top_results

    async def _get_graph_results(self, state: dict) -> list[SearchResult]:
        """Fetch GraphRAG results if entities are available."""
        if not self._graph_rag:
            return []
        entities = state.get("analysis").entities if state.get("analysis") else []
        if not entities:
            return []
        try:
            return await self._graph_rag.traverse(entities, top_k=10, time_range=state.get("time_range"))
        except Exception:
            return []

    # ── Merge streams with RRF ──────────────────────────────────────────

    def _merge_streams(
        self, names: list[str], results_raw: list, intent: str, top_k: int,
        time_range: TimeRange | None = None,
    ) -> tuple[dict[str, list[SearchResult]], list[SearchResult]]:
        """Merge multiple retrieval streams via RRF + diversity + time filter."""
        from llm_wiki.application.use_cases.query.pipeline import (
            _build_rrf_weights,
            _weighted_rrf_fusion,
            _diversify,
            _enforce_time_boundary,
        )

        result_sets: dict[str, list[SearchResult]] = {}
        for name, raw in zip(names, results_raw):
            if isinstance(raw, Exception):
                logger.warning("Stream '%s' failed: %s", name, raw)
                result_sets[name] = []
            else:
                result_sets[name] = raw if isinstance(raw, list) else []

        weights = _build_rrf_weights(intent)
        # Add graph weight if graph stream exists
        if "graph" in names:
            weights["graph"] = 0.6

        merged = _weighted_rrf_fusion(result_sets, weights)
        diversified = _diversify(merged, max_per_source=5, max_per_page=2)
        in_range = _enforce_time_boundary(diversified, time_range)
        return result_sets, in_range[:top_k]

    # ── Stream helpers ──────────────────────────────────────────────────

    def _build_messages(self, input: QueryInput, state: dict) -> list[dict]:
        """Build LLM messages from state (shared by _generate and execute_stream)."""
        today_str = now().strftime("%Y-%m-%d")
        intent = state["intent"]
        analysis = state.get("analysis")
        lang = analysis.language if analysis and analysis.language else "vi"

        # Intent-specific system prompt per language
        if lang == "en":
            _INTENT_EN_PROMPTS = {
                "timeline": (
                    "You are an expert in chronological timeline analysis. "
                    "Answer the question based ON THE PROVIDED CONTEXT. "
                    "SORT your answer CHRONOLOGICALLY. "
                    "Each event milestone MUST have a specific date (day-month-year). "
                    "Use format: **Date:** Event → Development → Impact.\n\n"
                ),
                "comparative": (
                    "You are an expert in COMPARATIVE analysis. "
                    "Answer the question based ON THE PROVIDED CONTEXT. "
                    "Present a clear SIDE-BY-SIDE COMPARISON. "
                    "Structure: (1) Similarities, (2) Differences, (3) Pros/cons of each. "
                    "Include specific DATES for each data point compared.\n\n"
                ),
                "current_state": (
                    "You are an expert in CURRENT STATE analysis. "
                    "Answer the question based ON THE PROVIDED CONTEXT. "
                    "PRIORITIZE the MOST RECENT information. "
                    "Include specific DATES for all information. "
                    "Cite exact figures when available.\n\n"
                ),
                "historical": (
                    "You are an expert in HISTORICAL EVENT analysis. "
                    "Answer the question based ON THE PROVIDED CONTEXT. "
                    "Include SPECIFIC DATES for all information. "
                    "Analyze CAUSE → DEVELOPMENT → IMPACT.\n\n"
                ),
                "general": (
                    "You are a deep-research assistant. "
                    "Answer the question based ON THE PROVIDED CONTEXT. "
                    "Your answer MUST include: "
                    "(1) a concise summary at the beginning; "
                    "(2) detailed analysis of each relevant aspect, "
                    "with specific examples from the context; "
                    "(3) explanation of connections between ideas; "
                    "(4) an overall conclusion at the end.\n\n"
                ),
            }
            system_prompt = _INTENT_EN_PROMPTS.get(intent, _INTENT_EN_PROMPTS["general"])
            system_prompt += (
                "Cite sources using [N]. "
                "If the context is insufficient, clearly state which parts "
                "are missing rather than fabricating. "
                "Be thorough and concise, not superficial.\n\n"
                f"IMPORTANT: Today is {today_str}. "
                "When answering, you MUST include specific dates (day-month-year) "
                "for all information. "
                "DO NOT use relative terms like 'recently', 'a few months ago'. "
                "Always cite exact dates from the provided context."
            )
        else:
            system_prompt = _INTENT_SYSTEM_PROMPTS.get(intent, _INTENT_SYSTEM_PROMPTS["general"])
            system_prompt += (
                "Trích dẫn nguồn bằng [N]. "
                "Nếu ngữ cảnh không đủ, hãy nêu rõ phần nào chưa có thông tin "
                "thay vì bịa đặt. "
                "Hãy trả lời đầy đủ, súc tích nhưng không sơ xài.\n\n"
                f"LƯU Ý QUAN TRỌNG: Hôm nay là {today_str}. "
                "Khi trả lời, PHẢI ghi rõ ngày tháng năm cụ thể cho mọi thông tin. "
                "KHÔNG được dùng từ tương đối như 'gần đây', 'mới đây'. "
                "Luôn trích dẫn ngày tháng chính xác từ ngữ cảnh được cung cấp."
            )

        from llm_wiki.application.use_cases.query.pipeline import _temporal_addendum
        system_prompt += _temporal_addendum(intent, language=lang)

        messages = [{"role": "system", "content": system_prompt}]
        for h in (input.chat_history or [])[-6:]:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({
            "role": "user",
            "content": f"Context:\n\n{state['context']}\n\nQuestion: {input.question}",
        })
        return messages

    # ── Generation (used by execute() and silent retry loop) ────────────────

    async def _generate(self, input: QueryInput, state: dict) -> dict:
        """Generate an answer from the retrieved context."""
        if not state["context"]:
            state["answer"] = "Không tìm thấy thông tin liên quan trong cơ sở dữ liệu."
            return state

        if not self._llm:
            state["answer"] = ""
            return state

        try:
            result = await self._llm.chat_completion_reasoning(
                messages=self._build_messages(input, state),
                temperature=0.3,
                max_tokens=16384,
            )
            state["answer"] = result.get("content", "")
        except Exception:
            try:
                state["answer"] = await self._llm.chat_completion(
                    messages=self._build_messages(input, state),
                    temperature=0.3, max_tokens=16384,
                )
            except Exception:
                state["answer"] = "Xin lỗi, không thể tạo câu trả lời. Vui lòng thử lại."

        return state

    async def execute_stream(self, input: QueryInput):
        """Run the reflective RAG pipeline with real token-by-token streaming.

        Streams the first attempt's answer as SSE tokens immediately.
        If evaluation shows quality is inadequate, runs silent reflection
        retries and yields the improved answer as a replacement.
        """
        t0 = time.time()
        telemetry = self._telemetry
        root_span: TelemetrySpan | None = None

        if telemetry:
            root_span = await telemetry.start_span(
                name="reflective_rag_stream",
                kind="chain",
                inputs={"question": input.question, "max_attempts": self._max_attempts},
            )
            self._root_span = root_span
            # Wire all traced wrappers under the root span so LangSmith
            # shows a single trace tree instead of isolated runs.
            from llm_wiki.application.use_cases.query.pipeline import _set_parent_on_wrappers
            _set_parent_on_wrappers(
                self._embedder, self._vector_search, self._keyword_search,
                self._llm, self._cache, root_span,
                rewriter=self._rewriter,
                analyzer=self._analyzer,
                event_search=self._event_search,
            )

        yield {"type": "status", "data": {"status": "processing"}}

        try:
            # ── Cache check ───────────────────────────────────────────────
            exact_cached = await self._try_exact_cache(
                input.question, source_id=input.source_id,
            )
            if exact_cached:
                inc_counter("query_total", {"status": "success", "cache": "exact"})
                yield {
                    "type": "complete",
                    "data": {
                        "answer": exact_cached.get("answer", ""),
                        "citations": exact_cached.get("sources", exact_cached.get("citations", [])),
                        "sources_used": exact_cached.get("sources_used", []),
                        "tokens_used": 0,
                        "cache_hit": True,
                    },
                }
                if telemetry and root_span:
                    await telemetry.end_span(
                        span=root_span,
                        outputs={
                            "answer_length": len(exact_cached.get("answer", "")),
                            "answer": exact_cached.get("answer", ""),
                        },
                        metadata={"cache_hit": True, "cache_type": "exact"},
                    )
                return

            # ── Initialise state ─────────────────────────────────────────
            state: dict[str, Any] = {
                "question": input.question,
                "rewritten_question": input.question,
                "intent": "general",
                "attempt": 0,
                "strategy": "default",
                "context": "",
                "top_results": [],
                "answer": "",
                "evaluation": None,
                "sources": [],
                "analysis": QueryAnalysis(),
                "time_range": None,
                "embedding": None,
                "hyde_embedding": None,
                "sub_results": [],
                "stop_reason": "max_attempts",
                "prev_content_ids": [],
                "prev_context": "",
            }

            # ── Pre-processing ───────────────────────────────────────────
            state = await self._pre_process(input, state)
            self._rebuild_search_adapters(state["intent"])

            # Semantic cache check
            if state.get("embedding"):
                sem_cached = await self._try_semantic_cache(state["embedding"])
                if sem_cached:
                    inc_counter("query_total", {"status": "success", "cache": "semantic"})
                    yield {
                        "type": "complete",
                        "data": {
                            "answer": sem_cached.get("answer", ""),
                            "citations": sem_cached.get("sources", sem_cached.get("citations", [])),
                            "sources_used": sem_cached.get("sources_used", []),
                            "tokens_used": 0,
                            "cache_hit": True,
                        },
                    }
                    if telemetry and root_span:
                        await telemetry.end_span(
                            span=root_span,
                            outputs={
                                "answer_length": len(sem_cached.get("answer", "")),
                                "answer": sem_cached.get("answer", ""),
                            },
                            metadata={"cache_hit": True, "cache_type": "semantic"},
                        )
                    return

            # ── Attempt 1: retrieve → stream tokens live → evaluate ─────
            state["attempt"] = 1
            state["strategy"] = "default"
            logger.info("Reflective stream: attempt 1, strategy=default")

            state = await self._retrieve_with_strategy(input, state)

            # Re-rank if available
            if self._re_ranker and state["top_results"] and len(state["top_results"]) > 5:
                state["top_results"] = await self._re_ranker.rerank(
                    state["rewritten_question"], state["top_results"], top_n=20,
                )

            state["context"] = self._pipeline._build_context(state["top_results"])
            state["prev_context"] = state["context"]
            state["prev_content_ids"] = [r.content_id for r in state["top_results"][:5]]

            if not state["context"]:
                no_context_answer = "Không tìm thấy thông tin liên quan trong cơ sở dữ liệu."
                yield {"type": "status", "data": {"status": "thinking"}}
                # Stream the empty answer as tokens
                for i in range(0, len(no_context_answer), 30):
                    yield {"type": "token", "data": no_context_answer[i:i + 30]}
                state["answer"] = no_context_answer
            elif self._llm:
                messages = self._build_messages(input, state)
                yield {"type": "status", "data": {"status": "thinking"}}

                # Real token streaming from LLM
                full_answer = ""
                try:
                    async for token in self._llm.chat_completion_stream(
                        messages=messages,
                        temperature=0.3,
                        max_tokens=16384,
                    ):
                        full_answer += token
                        yield {"type": "token", "data": token}
                except Exception:
                    full_answer = ""  # reset so fallback trigger works

                # Fallback: when the stream yields zero tokens (e.g.
                # reasoning model emits everything in reasoning_content
                # deltas), fall back to non-streaming so the user still
                # gets an answer.
                if not full_answer:
                    try:
                        result = await self._llm.chat_completion(
                            messages=messages, temperature=0.3, max_tokens=16384,
                        )
                        full_answer = result if isinstance(result, str) else result.get("content", "")
                        # Chunk the fallback result
                        for i in range(0, len(full_answer), 30):
                            yield {"type": "token", "data": full_answer[i:i + 30]}
                    except Exception:
                        full_answer = "Xin lỗi, không thể tạo câu trả lời. Vui lòng thử lại."
                        for i in range(0, len(full_answer), 30):
                            yield {"type": "token", "data": full_answer[i:i + 30]}

                state["answer"] = full_answer

            # Evaluate the first answer
            yield {"type": "status", "data": {"status": "summarizing"}}
            state["evaluation"] = await self._evaluate(state)
            state["evaluation"] = self._check_source_diversity(state["top_results"], state["evaluation"])

            # ── If answer is good, yield complete and return ────────────
            if state["evaluation"].should_stop:
                state["stop_reason"] = "quality_ok"
                state["sources"] = self._build_sources(state["top_results"])
                yield {"type": "status", "data": {"status": "caching"}}
                yield self._build_stream_complete(state)

                # Save cache + emit metrics
                await self._save_cache(
                    question=input.question, source_id=input.source_id,
                    answer=state["answer"], sources=state["sources"],
                    embedding=state.get("embedding"),
                    tokens_used=(getattr(self._llm, "last_usage", None) or {}).get("total_tokens", 0) if self._llm else 0,
                    attempts=state["attempt"],
                )
                self._emit_metrics(state, (time.time() - t0) * 1000)
                if telemetry and root_span:
                    await telemetry.end_span(
                        span=root_span,
                        outputs={"answer_length": len(state["answer"]), "answer": state["answer"]},
                        metadata={
                            "attempts": 1, "stop_reason": "quality_ok",
                            "intent": state["intent"],
                            "faithfulness": state["evaluation"].faithfulness,
                            "completeness": state["evaluation"].completeness,
                        },
                    )
                return

            # ── Answer needs refinement — notify user and retry ─────────
            logger.info(
                "Stream attempt 1: F=%.1f C=%.1f below threshold, refining...",
                state["evaluation"].faithfulness, state["evaluation"].completeness,
            )
            yield {"type": "status", "data": {"status": "refining"}}

            # Pick next strategy
            next_strategy = state["evaluation"].suggested_strategy or ""
            if next_strategy == "refine_query":
                refined = state["evaluation"].refined_query
                if refined and refined != state["rewritten_question"]:
                    state["rewritten_question"] = refined
                    if self._embedder:
                        try:
                            state["embedding"] = await self._embedder.embed(refined)
                        except Exception:
                            pass
                next_strategy = self._next_strategy(state["strategy"])
            elif next_strategy == state["strategy"]:
                next_strategy = self._next_strategy(state["strategy"])
            state["strategy"] = next_strategy

            # ── Attempts 2+ (silent) ─────────────────────────────────────
            while state["attempt"] < self._max_attempts:
                state["attempt"] += 1
                logger.info(
                    "Reflective stream retry %d/%d, strategy=%s",
                    state["attempt"], self._max_attempts, state["strategy"],
                )

                state = await self._retrieve_with_strategy(input, state)

                current_content_ids = [r.content_id for r in state["top_results"][:5]]
                results_changed = current_content_ids != state.get("prev_content_ids", [])
                state["prev_content_ids"] = current_content_ids

                if self._re_ranker and state["top_results"] and len(state["top_results"]) > 5:
                    if results_changed or state["attempt"] == 2:
                        state["top_results"] = await self._re_ranker.rerank(
                            state["rewritten_question"], state["top_results"], top_n=20,
                        )

                state["context"] = self._pipeline._build_context(state["top_results"])
                context_changed = state["context"] != state.get("prev_context", "")
                state["prev_context"] = state["context"]

                state = await self._generate(input, state)

                if context_changed or state["attempt"] == 2:
                    state["evaluation"] = await self._evaluate(state)
                elif state["evaluation"] is None:
                    state["evaluation"] = await self._evaluate(state)

                state["evaluation"] = self._check_source_diversity(state["top_results"], state["evaluation"])

                if state["evaluation"].should_stop:
                    state["stop_reason"] = "quality_ok"
                    break

                next_strategy = state["evaluation"].suggested_strategy or ""
                if next_strategy == "refine_query":
                    refined = state["evaluation"].refined_query
                    if refined and refined != state["rewritten_question"]:
                        state["rewritten_question"] = refined
                        if self._embedder:
                            try:
                                state["embedding"] = await self._embedder.embed(refined)
                            except Exception:
                                pass
                    next_strategy = self._next_strategy(state["strategy"])
                elif next_strategy == state["strategy"]:
                    next_strategy = self._next_strategy(state["strategy"])
                state["strategy"] = next_strategy

            # ── Yield the improved final answer as replacement ──────────
            state["sources"] = self._build_sources(state["top_results"])
            if state["attempt"] > 1 or state["strategy"] != "default":
                # Only send a new answer if it actually changed
                yield self._build_stream_complete(state)

            # Save cache
            await self._save_cache(
                question=input.question, source_id=input.source_id,
                answer=state["answer"], sources=state["sources"],
                embedding=state.get("embedding"),
                tokens_used=(getattr(self._llm, "last_usage", None) or {}).get("total_tokens", 0) if self._llm else 0,
                attempts=state["attempt"],
            )
            self._emit_metrics(state, (time.time() - t0) * 1000)

            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span,
                    outputs={
                        "answer_length": len(state["answer"]),
                        "answer": state["answer"],
                        "attempts": state["attempt"],
                        "stop_reason": state["stop_reason"],
                    },
                    metadata={
                        "attempts": state["attempt"],
                        "final_strategy": state["strategy"],
                        "intent": state["intent"],
                        "stop_reason": state["stop_reason"],
                        "total_latency_ms": round((time.time() - t0) * 1000, 2),
                        "faithfulness": state["evaluation"].faithfulness if state["evaluation"] else None,
                        "completeness": state["evaluation"].completeness if state["evaluation"] else None,
                    },
                )

        except Exception as exc:
            inc_counter("query_total", {"status": "error", "cache": "reflective_failure"})
            if telemetry and root_span:
                await telemetry.end_span(
                    span=root_span, error=str(exc),
                    metadata={"error_type": type(exc).__name__},
                )
            # Always yield a "complete" event so the frontend can
            # stop the loading indicator and display what we have.
            error_answer = f"Xin lỗi, đã xảy ra lỗi khi xử lý câu hỏi: {exc}"
            yield {
                "type": "complete",
                "data": {
                    "answer": error_answer,
                    "citations": [],
                    "sources_used": [],
                    "tokens_used": 0,
                    "error": str(exc),
                },
            }

    def _build_stream_complete(self, state: dict) -> dict:
        """Build a ``complete`` SSE event from pipeline state."""
        sources = self._build_sources(state["top_results"])
        citations = [
            {
                "page_title": s.get("page_title") or s.get("title", ""),
                "page_slug": s.get("page_slug") or s.get("id", ""),
                "section": "",
                "source_name": s.get("source_name") or "",
                "source_url": "",
                "timestamp": s.get("published_at") or "",
            }
            for s in sources
        ]
        return {
            "type": "complete",
            "data": {
                "answer": state["answer"],
                "citations": citations,
                "sources_used": [],
                "tokens_used": (getattr(self._llm, "last_usage", None) or {}).get("total_tokens", 0) if self._llm else 0,
                "attempts": state["attempt"],
                "stop_reason": state["stop_reason"],
            },
        }

    # ── Evaluation ──────────────────────────────────────────────────────

    async def _evaluate(self, state: dict) -> AnswerEvaluation:
        """Evaluate the current answer with LLM-as-judge."""
        if not state["answer"] or not state["context"]:
            return AnswerEvaluation(
                faithfulness=0.0, completeness=0.0, relevance=0.0,
                should_stop=True, missing_info="Empty answer or context",
            )

        try:
            return await self._evaluator.evaluate(
                question=state["question"],
                context=state["context"],
                answer=state["answer"],
                intent=state["intent"],
            )
        except Exception:
            # Evaluation failure → stop to avoid infinite loop
            return AnswerEvaluation(should_stop=True, missing_info="Evaluation failed")

    # ── Strategy helpers ────────────────────────────────────────────────

    @staticmethod
    def _next_strategy(current: str) -> str:
        """Cycle through strategies deterministically.

        Order: default → hyde → decompose → expand → (back to default).
        ``refine_query`` is handled specially in the main loop — it's
        never returned from this cycler.
        """
        order = ["default", "hyde", "decompose", "expand"]
        try:
            idx = order.index(current)
            return order[(idx + 1) % len(order)]
        except ValueError:
            return "default"

    @staticmethod
    def _check_source_diversity(
        top_results: list[SearchResult], evaluation: AnswerEvaluation,
    ) -> AnswerEvaluation:
        """Check that top results span multiple distinct pages.

        When all results come from ≤1 page, the answer is likely biased
        toward a single source. We downgrade relevance and suggest expand
        to encourage broader retrieval.
        """
        if not top_results or not evaluation:
            return evaluation

        distinct_pages: set[str] = set()
        for r in top_results:
            page = (r.metadata or {}).get("page_title") or r.title
            if page:
                distinct_pages.add(page)

        if len(distinct_pages) < _MIN_DISTINCT_SOURCES and len(top_results) >= 3:
            logger.debug(
                "Source diversity low: %d distinct pages from %d results",
                len(distinct_pages), len(top_results),
            )
            # Downgrade relevance by 2 points (clamped at 0) and push toward expand
            adjusted_relevance = max(0.0, evaluation.relevance - 2.0)
            evaluation.relevance = adjusted_relevance
            # Force should_stop=False unless already stopping on hard grounds
            if evaluation.faithfulness >= _MIN_FAITHFULNESS and evaluation.completeness >= _MIN_COMPLETENESS:
                # Quality is fine despite low diversity — don't force retry
                pass
            elif adjusted_relevance < 5 and evaluation.suggested_strategy == "refine_query":
                # Low diversity + low relevance → try expand instead
                evaluation.suggested_strategy = "expand"
            elif adjusted_relevance < 5:
                evaluation.suggested_strategy = "expand"

        return evaluation

    async def _generate_hyde(self, question: str) -> str:
        """Generate a hypothetical answer document for HyDE retrieval."""
        if not self._llm:
            return question
        try:
            return await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": _HYDE_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Câu hỏi: {question}"},
                ],
                temperature=0.7,
                max_tokens=300,
            )
        except Exception:
            return question

    async def _decompose(self, question: str) -> list[str]:
        """Decompose a complex question into sub-queries."""
        if not self._llm:
            return []
        try:
            raw = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": _DECOMPOSE_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Câu hỏi: {question}"},
                ],
                temperature=0.0,
                max_tokens=200,
            )
            parsed = json.loads(raw.strip())
            sub_queries = parsed.get("sub_queries", [])
            if isinstance(sub_queries, list) and sub_queries:
                logger.debug("Decomposed into %d sub-queries: %s", len(sub_queries), sub_queries)
                return sub_queries
            return []
        except Exception:
            return []

    # ── Sources ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_sources(top_results: list[SearchResult]) -> list[dict]:
        """Build the sources array for the API response."""
        return [
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

    # ── Metrics ─────────────────────────────────────────────────────────

    def _emit_metrics(self, state: dict, latency_ms: float) -> None:
        """Emit Prometheus business metrics for this pipeline run."""
        inc_counter("query_total", {"status": "success", "cache": "reflective"})
        inc_counter("reflection_attempts", {"stop_reason": state["stop_reason"]}, value=state["attempt"])
        if state["evaluation"]:
            from llm_wiki.infrastructure.telemetry.metrics_collector import get_metrics
            try:
                get_metrics().histogram("reflection_faithfulness", state["evaluation"].faithfulness)
                get_metrics().histogram("reflection_completeness", state["evaluation"].completeness)
                get_metrics().histogram("reflection_relevance", state["evaluation"].relevance)
            except Exception:
                pass


class SelfReflectiveAskQuestionUseCase:
    """Use case wrapping the reflective pipeline for the non-streaming endpoint."""

    def __init__(self, pipeline: SelfReflectiveRAGPipeline):
        self._pipeline = pipeline

    async def execute(self, input: QueryInput) -> dict:
        return await self._pipeline.execute(input)


class SelfReflectiveStreamAnswerUseCase:
    """Use case wrapping the reflective pipeline for the streaming endpoint.

    Streams tokens in real-time from the first attempt's LLM generation.
    If quality is below thresholds, runs silent background refinement
    and yields the improved answer as a replacement.
    """

    def __init__(self, pipeline: SelfReflectiveRAGPipeline):
        self._pipeline = pipeline

    async def execute(self, input: QueryInput):
        async for chunk in self._pipeline.execute_stream(input):
            yield chunk
