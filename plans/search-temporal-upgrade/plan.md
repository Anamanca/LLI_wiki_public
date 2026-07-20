---
title: "Nâng cấp search pipeline với temporal filtering và query analysis"
description: "Triển khai query intent analysis, time-range extraction và temporal filtering trong vector/keyword search để cải thiện trả lờicâu hỏi có yếu tố thờigian (ví dụ: 'tình hình cổ phiếu 1 tháng vừa qua')."
status: pending
priority: P1
branch: "main"
tags: ["search", "temporal-filtering", "query-analysis", "clean-architecture"]
blockedBy: []
blocks: []
created: "2026-07-15T08:19:08.990Z"
createdBy: "ck:plan"
source: skill
---

# Nâng cấp search pipeline với temporal filtering và query analysis

## Overview

Hiện tại `QueryPipeline` chỉ thực hiện vector search + keyword search đơn thuần trên `page_sections`, không hiểu yếu tố thờigian trong câu hỏi. Kết quả là các câu hỏi như "tình hình cổ phiếu hiện tại thế nào, trong 1 tháng vừa qua" retrieve được dữ liệu cũ, và LLM phải trả lờikhông có dữ liệu phù hợp.

Plan này thiết kế và triển khai nâng cấp search theo đúng tinh thần Clean Architecture của project (`domain` → `application` → `infrastructure` → `presentation`):

- Thêm `QueryAnalyzerPort` để trích xuất `intent`, `time_range`, `entities` từ câu hỏi.
- Mở rộng `VectorSearchPort` / `KeywordSearchPort` để hỗ trợ `TimeRange` filter.
- Nâng cấp `PgVectorSearchAdapter` và `TsVectorSearchAdapter` lọc theo `pages.published_at`.
- Tích hợp analyzer vào `QueryPipeline`, cache key aware của ngày hiện tại.
- Cập nhật API contract, tests và tài liệu.

Kế hoạch được chia thành 6 phase, có thể thực hiện tuần tự.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Research & Design](./phase-01-research-design.md) | Pending |
| 2 | [Domain & Application Layer](./phase-02-domain-application-layer.md) | Pending |
| 3 | [Infrastructure Adapters](./phase-03-infrastructure-adapters.md) | Pending |
| 4 | [Pipeline Integration & Routes](./phase-04-pipeline-integration-routes.md) | Pending |
| 5 | [Tests & Validation](./phase-05-tests-validation.md) | Pending |
| 6 | [Documentation & Contract Update](./phase-06-documentation-contract-update.md) | Pending |

## Dependencies

- Phase 2 phụ thuộc Phase 1 (domain model & port design được phê duyệt).
- Phase 3 phụ thuộc Phase 2 (ports đã ổn định).
- Phase 4 phụ thuộc Phase 3 (adapters đã sẵn sàng).
- Phase 5 phụ thuộc Phase 4 (pipeline hoàn chỉnh để test).
- Phase 6 phụ thuộc Phase 5 (tests pass mới cập nhật docs).

---

# Prediction Report: Nâng cấp search pipeline với temporal filtering

## Verdict: CAUTION — proceed with refinements below

### Agreements (all 5 personas align)

1. **Time filter is the correct root-cause fix** — adding temporal awareness to search pipeline directly addresses the problem instead of relying on LLM to detect stale dates in retrieved docs.
2. **Optional analyzer is the right pattern** — `QueryAnalyzerPort = None` with graceful degradation follows existing DI patterns in `dependencies.py`.
3. **Backward-compatible API change** — adding optional `time_range` to ports and `history` to schema doesn't break existing clients.
4. **DB index on `pages.published_at` is essential** — without it, the new WHERE clause will cause full scan on every search.

### Conflicts & Resolutions

| Topic | Resolution |
|-------|------------|
| **LLM vs regex for date extraction** | Use regex-based **pre-filter** first (catch "1 tháng", "tuần qua", "năm 2024", relative dates in Vietnamese). Only call LLM when regex fails or question is ambiguous. This saves $0 and ~200ms for 80% of time queries. **Phase 2 should define `QueryAnalyzerPort` as a chain: `RegexAnalyzer → LLMAnalyzer` fallback.** |
| **JOIN pages in every search** | Only JOIN+WHERE when `time_range` is provided. When `time_range=None`, use existing queries without JOIN to avoid unnecessary overhead. **Phase 3 must implement two code paths in adapters.** |
| **Cache key change invalidates all caches** | Accept as necessary. The 1h TTL means impact lasts <1h. Add logging to track cache miss rate post-deploy. **No action required.** |
| **NULL `published_at` handling** | Current plan includes `OR p.published_at IS NULL` which is correct (no data loss). But should also **boost published pages** in ORDER BY so recent pages surface above NULL ones. **Phase 3 should add `ORDER BY p.published_at DESC NULLS LAST, similarity DESC` when time_range is set.** |
| **What if `published_at` data is wrong/old** | Wiki pages from YouTube transcripts may have `published_at` = video upload date (could be years old even if content is evergreen). **Phase 1 should verify data quality before implementing filter. If >50% pages have `published_at` > 6 months old, time filter will return empty results for "1 tháng vừa qua" regardless.** |

### Risk Summary

| Risk | Severity | Mitigation |
|------|----------|------------|
| DB has no recent data (<30 days) → time filter returns empty | **Critical** | Run data audit FIRST: `SELECT count(*) FROM pages WHERE published_at >= NOW() - INTERVAL '30 days'`. If result < 10 pages, plan is useless until new data is ingested. |
| Regex analyzer misses complex Vietnamese time expressions | Medium | Regex + LLM chain as described above. Log regex misses to improve patterns. |
| LLM analyzer every query adds 200ms+ latency | Medium | Implement regex pre-filter. Add analyzer result cache (TTL 5min per question hash). |
| `pages.published_at` index missing | High | Add migration before deploying code. Verify with `EXPLAIN ANALYZE`. |
| Port signature change breaks unit tests | Low | Update all mocks in test files (Phase 5 already accounts for this). |

### Recommendations

1. **BEFORE writing any code: audit DB data freshness.**  
   Run `SELECT count(*), date_trunc('week', COALESCE(published_at, created_at)) AS week FROM pages GROUP BY week ORDER BY week DESC LIMIT 10`. If no pages have `published_at` within 30 days, the filter will make search *worse* (return empty for "last month" queries).

2. **Use regex pre-filter before LLM analyzer.**  
   Vietnam-specific regex handles 80% of time queries. LLM is fallback only. This cuts per-query latency from ~200ms to ~1ms for most queries.

3. **Add analyzer result cache.**  
   Cache `QueryAnalysis` per question hash (TTL 5min) to avoid calling LLM for repeated questions.

4. **Only JOIN pages when filtering.**  
   Preserve existing fast queries when `time_range=None`. Add JOIN+WHERE only when filter is active.

5. **Verify data integrity first.**  
   If `published_at` is unreliable, consider using `source_items.published_at` or `event_observations.source_published_at` as alternative timestamp source.

6. **Consider adding `ORDER BY COALESCE(published_at, created_at) DESC` as tiebreaker** even without time filter, to boost recent content in all searches (recency awareness with zero extra LLM cost).
