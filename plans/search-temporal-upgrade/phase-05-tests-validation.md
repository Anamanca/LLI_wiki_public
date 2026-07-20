---
phase: 5
title: "Tests & Validation"
status: pending
priority: P1
effort: "4h"
dependencies: [4]
---

# Phase 5: Tests & Validation

## Overview

Cập nhật tất cả tests hiện tại và viết tests mới để đảm bảo pipeline hoạt động đúng. Chạy contract tests sau khi thay đổi.

## Requirements

- Functional:
  - Unit test cho `LLMQueryAnalyzerAdapter`.
  - Unit test cho `QueryPipeline` với mock analyzer.
  - Unit test cho `PgVectorSearchAdapter` với `time_range`.
  - Unit test cho `TsVectorSearchAdapter` với `time_range`.
  - Contract test via `tests/test_all_apis.py`.
- Non-functional:
  - Tất cả tests pass (cả cũ và mới).
  - Code coverage không giảm.

## Implementation Steps

### 5.1. Unit test cho `LLMQueryAnalyzerAdapter`

File: `tests/unit/infrastructure/test_query_analyzer_adapter.py`

```python
import pytest
from unittest.mock import AsyncMock

from llm_wiki.infrastructure.llm.query_analyzer_adapter import LLMQueryAnalyzerAdapter
from llm_wiki.domain.entities.query_analysis import QueryAnalysis
from llm_wiki.domain.value_objects.query_intent import QueryIntent

@pytest.mark.asyncio
async def test_analyze_current_state_with_time_range():
    mock_llm = AsyncMock()
    mock_llm.chat_completion.return_value = json.dumps({
        "intent": "current_state",
        "time_range": {"start": "2026-06-15", "end": "2026-07-15"},
        "entities": [{"name": "HPG", "type": "stock_ticker"}],
    })
    analyzer = LLMQueryAnalyzerAdapter(mock_llm)
    result = await analyzer.analyze("tình hình cổ phiếu 1 tháng vừa qua")
    assert result.intent == QueryIntent.CURRENT_STATE
    assert result.time_range is not None
    assert len(result.entities) == 1

@pytest.mark.asyncio
async def test_analyze_fails_gracefully():
    mock_llm = AsyncMock()
    mock_llm.chat_completion.side_effect = Exception("timeout")
    analyzer = LLMQueryAnalyzerAdapter(mock_llm)
    result = await analyzer.analyze("test question")
    assert result.intent == QueryIntent.GENERAL
    assert result.time_range is None
```

### 5.2. Unit test cho `QueryPipeline` với analyzer mock

File: `tests/unit/application/test_query_pipeline.py` (mở rộng)

```python
@pytest.mark.asyncio
async def test_query_pipeline_with_time_range_filter(
    mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    from llm_wiki.infrastructure.llm.query_analyzer_adapter import LLMQueryAnalyzerAdapter
    from llm_wiki.domain.value_objects.time_range import TimeRange
    from datetime import datetime, timedelta

    analyzer_llm = AsyncMock()
    analyzer_llm.chat_completion.return_value = json.dumps({
        "intent": "current_state",
        "time_range": {"start": "2026-06-15", "end": "2026-07-15"},
        "entities": [],
    })
    analyzer = LLMQueryAnalyzerAdapter(analyzer_llm)

    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
        query_analyzer=analyzer,
    )
    result = await pipeline.execute(QueryInput(question="tình hình cổ phiếu 1 tháng qua"))
    assert "answer" in result
    # Verify time_range was passed to search
    mock_vector_search.search_similar.assert_called_once()
    call_kwargs = mock_vector_search.search_similar.call_args.kwargs
    assert call_kwargs["time_range"] is not None
    assert call_kwargs["time_range"].start is not None

@pytest.mark.asyncio
async def test_cache_key_includes_date():
    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
        query_analyzer=None,
    )
    key1 = pipeline._cache_key("câu hỏi test")
    key2 = pipeline._cache_key("câu hỏi test")
    assert key1 == key2  # Same day → same key
```

### 5.3. Chạy tests

```bash
# Unit tests
pytest tests/unit/ -v

# Contract tests
API_BASE_URL=http://localhost:8000 pytest tests/test_all_apis.py -v

# Lint
PYTHONPATH=src:. ruff check . && ruff format .

# Type check
mypy src/llm_wiki
```

### 5.4. Manual validation scenarios

| Scenario | Question | Expected |
|---|---|---|
| Time-aware | "tình hình cổ phiếu HPG 1 tháng vừa qua" | Only pages with `published_at` within 30 days |
| No time hint | "HPG là công ty gì" | General search, no time filter |
| General fallback | (analyzer fail) | Behaves like current system |
| NULL published_at | (page without date) | Still included in results |

## Success Criteria

- [ ] Tất cả unit tests pass.
- [ ] Contract tests (`tests/test_all_apis.py`) pass.
- [ ] `ruff check .` clean.
- [ ] `mypy src/llm_wiki` clean.
- [ ] Manual validation 4 scenarios above pass.

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Mock không khớp signature mới | Medium | Cập nhật tất cả mock sau khi thay đổi ports |
| Contract test phát hiện thay đổi API shape | Low | Do thêm fields optional, backward compatible |
