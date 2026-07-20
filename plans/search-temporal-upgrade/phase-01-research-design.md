---
phase: 1
title: "Research & Design"
status: pending
priority: P1
effort: "4h"
dependencies: []
---

# Phase 1: Research & Design

## Overview

Phân tích sâu codebase hiện tại và tham khảo implementation tại `/home/hieunt/29_LLM_wiki` để xác định giải pháp phù hợp nhất với kiến trúc Clean Architecture của project. Đầu ra của phase này là domain model, port definitions, và data flow được team đồng thuận.

## Requirements

- Functional:
  - Hệ thống phải hiểu được yếu tố thờigian trong câu hỏi tiếng Việt ("1 tháng vừa qua", "hiện nay", "năm 2024", ...).
  - Search phải lọc được dữ liệu theo khoảng thờigian trên `pages.published_at`.
  - Hệ thống vẫn hoạt động khi analyzer fail (graceful degradation).
- Non-functional:
  - Không phá vỡ layer rules hiện tại: `domain` không import framework, `application` chỉ phụ thuộc `domain` và ports.
  - Giữ API contract cũ (không bắt buộc client thay đổi request).
  - Latency của analyzer phải < 200ms (LLM flash model) hoặc có timeout.

## Architecture

### Data Flow đề xuất

```
User question
    │
    ▼
┌─────────────────────┐
│ QueryAnalyzerPort   │  ← LLM hoặc regex-based
│  (intent, time_range, entities)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ QueryPipeline       │
│  - embed question   │
│  - vector search    │ with time_range
│  - keyword search   │ with time_range
│  - RRF merge        │
│  - synthesize       │
└─────────────────────┘
```

### Key Design Decisions

1. **Analyzer bằng LLM nhỏ**: Dùng `LLMClientPort` hiện có với prompt ngắn, temperature=0, max_tokens=200. Output JSON.
2. **TimeRange là domain value object**: Tận dụng `domain/value_objects/time_range.py` đã có, bổ sung factory methods.
3. **Mở rộng ports thay vì tạo ports mới**: Thêm `time_range: Optional[TimeRange]` vào `search_similar` và `search_keyword` để giảm số lượng thay đổi.
4. **Filter trên `pages.published_at`**: `page_sections` không có `published_at`, nên join `pages` và lọc ở đó.
5. **Graceful degradation**: Nếu analyzer fail hoặc không trả về `time_range`, pipeline chạy như cũ.

## Related Code Files

- Read:
  - `src/llm_wiki/application/use_cases/query/pipeline.py`
  - `src/llm_wiki/application/ports/search/vector_search.py`
  - `src/llm_wiki/infrastructure/search/pgvector_adapter.py`
  - `src/llm_wiki/infrastructure/search/tsvector_adapter.py`
  - `src/llm_wiki/domain/value_objects/time_range.py`
  - `src/llm_wiki/infrastructure/persistence/postgres/models.py`
- Reference:
  - `/home/hieunt/29_LLM_wiki/backend/app/services/query_analyzer.py`
  - `/home/hieunt/29_LLM_wiki/backend/app/services/multi_retriever.py`
  - `/home/hieunt/29_LLM_wiki/backend/app/services/synthesizer.py`

## Implementation Steps

1. **CRITICAL: Audit DB data freshness trước khi viết code.**
   ```sql
   SELECT count(*), date_trunc('week', COALESCE(published_at, created_at)) AS week
   FROM pages
   GROUP BY week ORDER BY week DESC LIMIT 10;
   
   SELECT count(*) FROM pages WHERE COALESCE(published_at, created_at) >= NOW() - INTERVAL '30 days';
   ```
   Nếu < 10 pages có dữ liệu trong 30 ngày, time filter sẽ khiến search **tệ hơn** (trả về rỗng). Cần ingest thêm data mới trước.
2. Đọc lại toàn bộ files liên quan và xác nhận kiến trúc hiện tại.
3. So sánh 3 phương án:
   - A) Chỉ thêm time_range filter vào search (tối thiểu).
   - B) Thêm Regex + LLM analyzer + time_range filter (đề xuất chính sau red-team).
   - C) B + event search + recency boost + entity graph (mở rộng).
4. Chọn phương án B cho Phase 1-4, để C cho phase sau nếu cần.
5. Xác định chính xác database indexes cần thiết: `CREATE INDEX ix_pages_published_at ON pages (COALESCE(published_at, created_at) DESC)`.
6. Viết ADR ngắn ghi lại quyết định (nếu project có `docs/adr/`).
7. Định nghĩa regex patterns cho Vietnamese time expressions làm pre-filter trước LLM.

## Success Criteria

- [ ] Tài liệu thiết kế domain model, ports, data flow được review.
- [ ] Quyết định phương án B được ghi nhận.
- [ ] Danh sách files cần tạo/sửa/xóa được xác nhận.
- [ ] DB index plan được xác nhận (cần thêm index trên `pages.published_at`).

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| LLM analyzer trả về sai date format | Medium | Parse defensive, fallback to general intent |
| `pages.published_at` NULL nhiều | Medium | Fallback dùng `pages.created_at` hoặc `source_items.published_at` |
| Cache key không aware time_range | Medium | Include current date trong cache key |
| Phá vỡ API contract | Low | Không thay đổi request schema, chỉ thêm optional fields |
| DB index thiếu gây chậm | Medium | Thêm index trước khi deploy |
