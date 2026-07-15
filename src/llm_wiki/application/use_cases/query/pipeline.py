import hashlib
import json
import logging
import time
from typing import Optional

from llm_wiki.application.dto.query_dto import QueryInput
from llm_wiki.application.ports.search.vector_search import (
    CacheServicePort,
    EmbeddingServicePort,
    KeywordSearchPort,
    LLMClientPort,
    VectorSearchPort,
)
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult

logger = logging.getLogger(__name__)


class QueryPipeline:
    def __init__(
        self,
        embedder: EmbeddingServicePort,
        vector_search: VectorSearchPort,
        keyword_search: KeywordSearchPort,
        llm: LLMClientPort,
        cache: CacheServicePort,
    ):
        self._embedder = embedder
        self._vector_search = vector_search
        self._keyword_search = keyword_search
        self._llm = llm
        self._cache = cache

    def _cache_key(self, question: str) -> str:
        return f"qa:{hashlib.sha256(question.encode()).hexdigest()}"

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
            context_parts.append(
                f"[{i}] ({source}) {result.title}\n{result.content[:800]}"
            )
        return "\n\n".join(context_parts)

    async def execute(self, input: QueryInput) -> dict:
        step_times: dict[str, float] = {}
        cache_hit = False

        cache_key = self._cache_key(input.question)
        t0 = time.time()
        cached = await self._cache.get(cache_key)
        step_times["cache_check"] = time.time() - t0
        if cached:
            cache_hit = True
            data = json.loads(cached)
            data["cache_hit"] = True
            data["pipeline_steps"] = step_times
            return data

        t0 = time.time()
        query_embedding = await self._embedder.embed(input.question)
        step_times["embed"] = time.time() - t0

        t0 = time.time()
        vector_results = await self._vector_search.search_similar(
            query_embedding, top_k=input.top_k * 2, source_id=input.source_id
        )
        step_times["vector_search"] = time.time() - t0

        t0 = time.time()
        keyword_results = await self._keyword_search.search_keyword(
            input.question, top_k=input.top_k
        )
        step_times["keyword_search"] = time.time() - t0

        t0 = time.time()
        merged = self._reciprocal_rank_fusion([vector_results, keyword_results])
        top_results = merged[:input.top_k]
        step_times["merge"] = time.time() - t0

        context = self._build_context(top_results)

        system_prompt = (
            "You are an expert assistant. Answer the question based ONLY on the provided context. "
            "Cite sources using [N] notation. If the context doesn't contain the answer, say so."
        )

        t0 = time.time()
        answer = await self._llm.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {input.question}"},
            ],
            temperature=0.3,
            max_tokens=2048,
        )
        step_times["synthesize"] = time.time() - t0

        sources = [
            {
                "id": r.content_id,
                "title": r.title,
                "score": round(r.score, 4),
                "page_title": r.metadata.get("page_title") if r.metadata else None,
                "page_slug": r.metadata.get("page_slug") if r.metadata else None,
            }
            for r in top_results[:5]
        ]

        result = {
            "answer": answer,
            "sources": sources,
            "tokens_used": 0,
            "cache_hit": cache_hit,
            "pipeline_steps": step_times,
        }

        t0 = time.time()
        await self._cache.set(cache_key, json.dumps(result, default=str), ttl=3600)
        step_times["cache_save"] = time.time() - t0

        return result

    async def execute_stream(self, input: QueryInput):
        step_times: dict[str, float] = {}

        t0 = time.time()
        query_embedding = await self._embedder.embed(input.question)
        step_times["embed"] = time.time() - t0

        t0 = time.time()
        vector_results = await self._vector_search.search_similar(
            query_embedding, top_k=input.top_k * 2, source_id=input.source_id
        )
        step_times["vector_search"] = time.time() - t0

        t0 = time.time()
        keyword_results = await self._keyword_search.search_keyword(
            input.question, top_k=input.top_k
        )
        step_times["keyword_search"] = time.time() - t0

        t0 = time.time()
        merged = self._reciprocal_rank_fusion([vector_results, keyword_results])
        top_results = merged[:input.top_k]
        step_times["merge"] = time.time() - t0

        context = self._build_context(top_results)

        system_prompt = (
            "You are an expert assistant. Answer the question based ONLY on the provided context. "
            "Cite sources using [N] notation. If the context doesn't contain the answer, say so."
        )

        yield {"type": "metadata", "data": {"pipeline_steps": step_times}}

        async for chunk in self._llm.chat_completion_stream(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {input.question}"},
            ],
            temperature=0.3,
            max_tokens=2048,
        ):
            yield {"type": "chunk", "data": chunk}

        sources = [
            {
                "id": r.content_id,
                "title": r.title,
                "score": round(r.score, 4),
                "page_title": r.metadata.get("page_title") if r.metadata else None,
                "page_slug": r.metadata.get("page_slug") if r.metadata else None,
            }
            for r in top_results[:5]
        ]

        yield {"type": "sources", "data": sources}
        yield {"type": "done", "data": None}
