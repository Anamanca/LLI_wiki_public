---
phase: 3
title: "Infrastructure Adapters"
status: pending
priority: P1
effort: "6h"
dependencies: [2]
---

# Phase 3: Infrastructure Adapters

## Overview

Implement concrete adapters cho các ports định nghĩa ở Phase 2. Đây là phần nặng nhất về mặt code.

**Red-team feedback incorporated:**
- Chỉ JOIN `pages` khi cần filter (time_range != None), giữ nguyên query cũ cho trường hợp không filter.
- Thêm `ORDER BY p.published_at DESC NULLS LAST` khi có time filter để boost recent content.
- Sử dụng `COALESCE(published_at, created_at)` làm fallback cho pages không có `published_at`.

## Requirements

- Functional:
  - `LLMQueryAnalyzerAdapter`: gọi LLM để phân tích câu hỏi, parse JSON response.
  - `PgVectorSearchAdapter`: thêm JOIN với `pages` và WHERE clause lọc `published_at`.
  - `TsVectorSearchAdapter`: tương tự, thêm WHERE clause lọc `published_at`.
- Non-functional:
  - Analyzer fail → trả về `QueryAnalysis(intent=GENERAL)`, không crash.
  - Timeout cho analyzer call ≤ 5s.
  - Vector search giữ được hiệu năng với index hiện tại.

## Implementation Steps

### 3.1. Tạo `infrastructure/llm/query_analyzer_adapter.py`

```python
import json
import logging
from datetime import datetime
from typing import Optional

from llm_wiki.application.ports.query.query_analyzer import QueryAnalyzerPort
from llm_wiki.application.ports.search.vector_search import LLMClientPort
from llm_wiki.domain.entities.query_analysis import QueryAnalysis
from llm_wiki.domain.value_objects.entity_ref import EntityRef
from llm_wiki.domain.value_objects.query_intent import QueryIntent
from llm_wiki.domain.value_objects.time_range import TimeRange

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Phân loại câu hỏi. Output JSON:
{"intent": "...", "time_range": {"start": "YYYY-MM-DD hoặc null", "end": "YYYY-MM-DD hoặc null"}, "entities": [{"name": "...", "type": "..."}]}

Intent: current_state|historical|timeline|comparative|general
Entity type: stock_ticker|commodity|location|macro_indicator|person|organization|policy
Chỉ output JSON, không markdown."""


class LLMQueryAnalyzerAdapter(QueryAnalyzerPort):
    def __init__(self, llm: LLMClientPort):
        self._llm = llm

    async def analyze(self, question: str) -> QueryAnalysis:
        try:
            response = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Câu hỏi: {question}"},
                ],
                temperature=0.0,
                max_tokens=200,
            )
            parsed = json.loads(response.strip())
            time_range = None
            raw_tr = parsed.get("time_range")
            if raw_tr:
                start = self._parse_date(raw_tr.get("start"))
                end = self._parse_date(raw_tr.get("end"))
                if start or end:
                    time_range = TimeRange(start=start or datetime.min, end=end)
            entities = [
                EntityRef(name=e.get("name", e) if isinstance(e, dict) else str(e),
                          type=e.get("type") if isinstance(e, dict) else None)
                for e in parsed.get("entities", [])
            ]
            return QueryAnalysis(
                intent=QueryIntent(parsed.get("intent", "general")),
                time_range=time_range,
                entities=entities,
            )
        except Exception as exc:
            logger.warning("Query analysis failed, returning general: %s", exc)
            return QueryAnalysis(intent=QueryIntent.GENERAL)

    def _parse_date(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
```

### 3.2. Nâng cấp `infrastructure/search/pgvector_adapter.py`

Thêm tham số `time_range` vào `search_similar`. Thêm JOIN và WHERE clause:

```python
async def search_similar(
    self,
    embedding: Embedding,
    top_k: int = 10,
    source_id: Optional[str] = None,
    time_range: Optional[TimeRange] = None,
) -> list[SearchResult]:
    vec_str = self._vector_to_str(embedding.vector)
    params: dict = {"vec": vec_str, "limit": top_k}

    where_parts = ["ps.section_vector IS NOT NULL"]
    if source_id:
        where_parts.append("ps.source_id = :source_id")
        params["source_id"] = source_id
    if time_range:
        where_parts.append("(p.published_at >= :start_date OR p.published_at IS NULL)")
        params["start_date"] = time_range.start
        if time_range.end:
            where_parts.extend(["p.published_at <= :end_date"])
            params["end_date"] = time_range.end

    where_sql = " AND ".join(where_parts)

    sql = text(f"""
        SELECT ps.id, ps.content_markdown AS content, ps.title AS heading_title,
               p.title AS page_title, p.slug AS page_slug, s.name AS source_name,
               p.published_at,
               1 - (ps.section_vector <=> :vec) AS similarity
        FROM page_sections ps
        JOIN pages p ON ps.page_id = p.id
        LEFT JOIN sources s ON ps.source_id = s.id
        WHERE {where_sql}
        ORDER BY ps.section_vector <=> :vec
        LIMIT :limit
    """)
    result = await self._session.execute(sql, params)
    rows = result.mappings().all()
    return [...]
```

**Quan trọng**: Để `p.published_at IS NULL` trong WHERE để các page chưa có `published_at` không bị loại. Dùng `ORDER BY` để ưu tiên page có `published_at` gần đây hơn nếu cần.

Tương tự cho `search_events_similar`: thêm time_range filter dựa trên `event_canonicals.normalized_date` hoặc `event_observations.source_published_at`.

### 3.3. Nâng cấp `infrastructure/search/tsvector_adapter.py`

```python
async def search_keyword(
    self,
    query: str,
    top_k: int = 10,
    time_range: Optional[TimeRange] = None,
) -> list[SearchResult]:
    cleaned = _clean_query(query)
    if not cleaned:
        return []

    params = {"query": cleaned, "limit": top_k}
    where_parts = ["ps.fts_vector @@ plainto_tsquery('simple', :query)"]
    if time_range:
        where_parts.append("(p.published_at >= :start_date OR p.published_at IS NULL)")
        params["start_date"] = time_range.start
        if time_range.end:
            where_parts.append("p.published_at <= :end_date")
            params["end_date"] = time_range.end

    sql = text(f"""
        SELECT ps.id, ps.content_markdown AS content, ps.title AS heading_title,
               p.title AS page_title, p.slug AS page_slug, s.name AS source_name,
               ts_rank(ps.fts_vector, plainto_tsquery('simple', :query)) AS similarity,
               p.published_at
        FROM page_sections ps
        JOIN pages p ON ps.page_id = p.id
        LEFT JOIN sources s ON ps.source_id = s.id
        WHERE {" AND ".join(where_parts)}
        ORDER BY similarity DESC
        LIMIT :limit
    """)
    result = await self._session.execute(sql, params)
    rows = result.mappings().all()
    return [...]
```

### 3.4. Thêm DB index (migration file nếu có Alembic)

Nếu project dùng Alembic, tạo migration thêm:
```sql
CREATE INDEX IF NOT EXISTS ix_pages_published_at ON pages (published_at DESC);
CREATE INDEX IF NOT EXISTS ix_pages_published_at_created ON pages (COALESCE(published_at, created_at) DESC);
```

## Success Criteria

- [ ] `LLMQueryAnalyzerAdapter` trả về `QueryAnalysis` có `intent` và `time_range`.
- [ ] Analyzer fail → `QueryAnalysis(intent=GENERAL, time_range=None)`.
- [ ] `PgVectorSearchAdapter.search_similar` lọc theo `time_range.start` và `time_range.end`.
- [ ] `TsVectorSearchAdapter.search_keyword` lọc theo `time_range`.
- [ ] Pages có `published_at=NULL` vẫn được include trong kết quả.

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| LLM analyzer latency cao | Medium | Timeout 5s, fallback to general |
| SQL injection qua tham số động | Low | Dùng bind params, không concatenate string |
| JOIN với pages làm chậm search | Medium | Đảm bảo index trên `pages.id` (PK đã có) và `pages.published_at` |
