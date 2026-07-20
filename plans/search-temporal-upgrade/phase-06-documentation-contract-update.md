---
phase: 6
title: "Documentation & Contract Update"
status: pending
priority: P2
effort: "2h"
dependencies: [5]
---

# Phase 6: Documentation & Contract Update

## Overview

Cập nhật tài liệu API, AGENTS.md, và các file liên quan để phản ánh nâng cấp mới.

## Requirements

- Functional:
  - `AGENTS.md` cập nhật "Critical Gotchas" về time_range.
  - `01_API_list.md` cập nhật nếu có tham số mới.
  - `frontend/types/index.ts` cập nhật nếu DTO thay đổi.
- Non-functional:
  - Tài liệu rõ ràng, developer mới có thể hiểu flow.

## Implementation Steps

### 6.1. Cập nhật `AGENTS.md`

Thêm vào phần "Critical Gotchas":
```markdown
- `QueryPipeline` auto-infers `TimeRange` from question via `QueryAnalyzerPort` (LLM-based).
  Analyzer failure → graceful degradation to general search without time filter.
- `Cache key` for Q&A includes current date (`YYYY-MM-DD`) to prevent stale results for
  time-sensitive questions.
- Vector search and keyword search now accept optional `TimeRange` parameter; adapters
  filter by `pages.published_at` (NULL values included to avoid data loss).
- Upgrading search adapters requires matching port signature changes in all mocks/tests.
```

### 6.2. Cập nhật `01_API_list.md` (nếu tồn tại)

Thêm note:
```markdown
### POST /api/query
- Request: `{"question": "...", "source_id": "...", "top_k": 10, "history": [...]}`
  - `history` (optional): list of `{"role": "...", "content": "..."}` for multi-turn context.
- New behavior: Pipeline auto-analyzes question for time range.
  - Questions like "1 tháng vừa qua" → `pages.published_at` filtered.
```

### 6.3. Cập nhật `frontend/types/index.ts` (nếu DTO thay đổi)

Nếu `QueryResponse` có thêm field `pipeline_steps.analyze`:
```typescript
export interface PipelineSteps {
  cache_check?: number;
  analyze?: number;  // new
  embed?: number;
  vector_search?: number;
  keyword_search?: number;
  merge?: number;
  synthesize?: number;
  cache_save?: number;
}
```

### 6.4. Cập nhật `tests/test_all_apis.py`

Đảm bảo contract test kiểm tra:
- Response vẫn có `answer`, `citations`, `sources_used`.
- `pipeline_steps` có key `analyze` khi analyzer chạy.
- Request với `history` được chấp nhận.

### 6.5. Tạo changelog ngắn

```markdown
## [Upcoming] Search Temporal Filtering
### Added
- `QueryAnalyzerPort` + `LLMQueryAnalyzerAdapter` for intent/time_range extraction.
- `TimeRange` support in `VectorSearchPort` and `KeywordSearchPort`.
- Temporal filtering in `PgVectorSearchAdapter` and `TsVectorSearchAdapter`.
- Date-aware cache key for time-sensitive questions.
- `history` field in `QueryRequest`.

### Changed
- `QueryPipeline` now accepts `QueryAnalyzerPort`.
- System prompt includes temporal constraints for time-sensitive questions.
```

## Success Criteria

- [ ] `AGENTS.md` updated with new gotchas.
- [ ] `01_API_list.md` updated (if exists).
- [ ] `frontend types` updated (if DTO changed).
- [ ] Contract test updated.

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Docs không đồng bộ với code | Low | Review sau khi code merge |
| Frontend không tương thích | Low | Các thay đổi đều backward-compatible |
