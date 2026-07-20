---
phase: 4
title: "Pipeline Integration & Routes"
status: pending
priority: P1
effort: "4h"
dependencies: [3]
---

# Phase 4: Pipeline Integration & Routes

## Overview

Tích hợp analyzer vào `QueryPipeline`, cập nhật routes và DI container. Đảm bảo pipeline mới vẫn hoạt động khi analyzer fail.

## Requirements

- Functional:
  - `QueryPipeline` nhận `QueryAnalyzerPort` (optional).
  - Trước khi search, gọi analyzer để lấy `time_range`.
  - Truyền `time_range` xuống `VectorSearchPort` và `KeywordSearchPort`.
  - Cache key include current date để tránh cache câu hỏi thời gian nhạy cảm.
  - `QueryRequest` schema hỗ trợ `history` (tùy chọn, cho tương lai).
- Non-functional:
  - Không phá vỡ API contract hiện tại.
  - Analyzer fail không làm hỏng toàn bộ pipeline.

## Implementation Steps

### 4.1. Nâng cấp `application/use_cases/query/pipeline.py`

```python
from llm_wiki.application.ports.query.query_analyzer import QueryAnalyzerPort
from llm_wiki.domain.entities.query_analysis import QueryAnalysis
from llm_wiki.domain.value_objects.time_range import TimeRange
from datetime import date

class QueryPipeline:
    def __init__(
        self,
        embedder: EmbeddingServicePort,
        vector_search: VectorSearchPort,
        keyword_search: KeywordSearchPort,
        llm: LLMClientPort,
        cache: CacheServicePort,
        query_analyzer: Optional[QueryAnalyzerPort] = None,
    ):
        self._embedder = embedder
        self._vector_search = vector_search
        self._keyword_search = keyword_search
        self._llm = llm
        self._cache = cache
        self._query_analyzer = query_analyzer

    def _cache_key(self, question: str) -> str:
        today = date.today().isoformat()
        return f"qa:{hashlib.sha256((question + today).encode()).hexdigest()}"

    async def _analyze(self, input: QueryInput) -> Optional[QueryAnalysis]:
        if not self._query_analyzer:
            return None
        if input.source_id:
            return None
        try:
            return await self._query_analyzer.analyze(input.question)
        except Exception as exc:
            logger.warning("Query analysis skipped: %s", exc)
            return None

    def _build_system_prompt(self, analysis: Optional[QueryAnalysis] = None) -> str:
        base = (
            "You are an expert assistant. Answer the question based ONLY on the "
            "provided context. Cite sources using [N] notation. If the context "
            "doesn't contain the answer, say so."
        )
        if not analysis:
            return base
        intent = analysis.intent.value
        if intent in ("current_state", "timeline"):
            return (
                base + "\n\nLƯU Ý: Câu hỏi có yếu tố THỜI GIAN. "
                "Với mọi số liệu, ghi rõ ngày tháng năm cụ thể. "
                "Ưu tiên thông tin mới nhất. "
                "KHÔNG dùng các từ mơ hồ như 'gần đây', 'mới đây'."
            )
        return base

    async def execute(self, input: QueryInput) -> dict:
        step_times: dict[str, float] = {}

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
        analysis = await self._analyze(input)
        time_range = analysis.time_range if analysis else None
        step_times["analyze"] = time.time() - t0

        t0 = time.time()
        query_embedding = await self._embedder.embed(input.question)
        step_times["embed"] = time.time() - t0

        t0 = time.time()
        vector_results = await self._vector_search.search_similar(
            query_embedding, top_k=input.top_k * 2,
            source_id=input.source_id, time_range=time_range,
        )
        step_times["vector_search"] = time.time() - t0

        t0 = time.time()
        keyword_results = await self._keyword_search.search_keyword(
            input.question, top_k=input.top_k, time_range=time_range,
        )
        step_times["keyword_search"] = time.time() - t0

        t0 = time.time()
        merged = self._reciprocal_rank_fusion([vector_results, keyword_results])
        top_results = merged[:input.top_k]
        step_times["merge"] = time.time() - t0

        context = self._build_context(top_results)
        system_prompt = self._build_system_prompt(analysis)

        t0 = time.time()
        answer = await self._llm.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Context:\n\n{context}\n\nQuestion: {input.question}"},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        step_times["synthesize"] = time.time() - t0

        sources = [...]
        result = {
            "answer": answer, "sources": sources, "tokens_used": 0,
            "cache_hit": False, "pipeline_steps": step_times,
        }

        t0 = time.time()
        await self._cache.set(cache_key, json.dumps(result, default=str), ttl=3600)
        step_times["cache_save"] = time.time() - t0
        return result
```

Tương tự cho `execute_stream`.

### 4.2. Cập nhật `presentation/schemas/common.py`

```python
class QueryRequest(BaseModel):
    question: str
    source_id: Optional[str] = None
    top_k: Optional[int] = 10
    stream: bool = False
    history: Optional[list[dict]] = None  # new, optional
```

### 4.3. Cập nhật `presentation/dependencies.py`

```python
from llm_wiki.infrastructure.llm.query_analyzer_adapter import LLMQueryAnalyzerAdapter

class Container(containers.DeclarativeContainer):
    ...
    query_analyzer = providers.Factory(
        LLMQueryAnalyzerAdapter,
        llm=llm_client,
    )
    query_pipeline = providers.Factory(
        QueryPipeline,
        embedder=embedder,
        vector_search=None,
        keyword_search=None,
        llm=llm_client,
        cache=cache,
        query_analyzer=query_analyzer,
    )
```

### 4.4. Cập nhật `presentation/routes/query.py`

Trong `get_query_pipeline`:
```python
def get_query_pipeline(db: AsyncSession = Depends(get_db)):
    embedder = container.embedder()
    llm = container.llm_client()
    cache = container.cache()
    analyzer = container.query_analyzer()
    return QueryPipeline(
        embedder=embedder,
        vector_search=PgVectorSearchAdapter(db),
        keyword_search=TsVectorSearchAdapter(db),
        llm=llm,
        cache=cache,
        query_analyzer=analyzer,
    )
```

## Success Criteria

- [ ] `QueryPipeline` nhận `QueryAnalyzerPort` và gọi phân tích trước khi search.
- [ ] `time_range` từ analyzer được truyền xuống `vector_search` và `keyword_search`.
- [ ] Cache key include ngày hiện tại, tránh stale cache cho câu hỏi thời gian.
- [ ] Analyzer fail → pipeline vẫn chạy bình thường với general intent.
- [ ] System prompt có temporal addendum khi intent là `current_state` hoặc `timeline`.
- [ ] `GET /search` endpoint cũng hỗ trợ `time_range` filter (tùy chọn).

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Analyzer làm chậm pipeline | Medium | Timeout 5s, fallback nhanh |
| Cache key thay đổi → tất cả cache miss | Low | Acceptable trade-off, cache expire sau 1h |
| Thay đổi signature ports → các mock test fail | Medium | Cập nhật tests trong Phase 5 |
