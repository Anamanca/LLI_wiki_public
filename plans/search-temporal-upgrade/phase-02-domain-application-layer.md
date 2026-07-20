---
phase: 2
title: "Domain & Application Layer"
status: pending
priority: P1
effort: "3h"
dependencies: [1]
---

# Phase 2: Domain & Application Layer

## Overview

Tạo các domain value objects/entities mới và định nghĩa ports. Đây là lớp trung tâm, không import bất kỳ framework nào, chỉ pure Python dataclasses và ABC.

## Requirements

- Functional:
  - `QueryIntent` enum với 5 intent types.
  - `EntityRef` value object.
  - `QueryAnalysis` entity dataclass.
  - `QueryAnalyzerPort` ABC.
  - Nâng cấp `VectorSearchPort` và `KeywordSearchPort` hỗ trợ `time_range`.
- Non-functional:
  - Không import FastAPI, SQLAlchemy, hay bất kỳ thư viện HTTP/ORM nào.
  - `TimeRange` đã có sẵn, chỉ bổ sung factory methods.

## Implementation Steps

### 2.1. Tạo `domain/value_objects/query_intent.py`
```python
from enum import Enum

class QueryIntent(str, Enum):
    CURRENT_STATE = "current_state"
    HISTORICAL = "historical"
    TIMELINE = "timeline"
    COMPARATIVE = "comparative"
    GENERAL = "general"
```

### 2.2. Tạo `domain/value_objects/entity_ref.py`
```python
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class EntityRef:
    name: str
    type: Optional[str] = None
```

### 2.3. Tạo `domain/entities/query_analysis.py`
```python
from dataclasses import dataclass, field
from typing import Optional, List

from llm_wiki.domain.value_objects.query_intent import QueryIntent
from llm_wiki.domain.value_objects.time_range import TimeRange
from llm_wiki.domain.value_objects.entity_ref import EntityRef

@dataclass
class QueryAnalysis:
    intent: QueryIntent = QueryIntent.GENERAL
    time_range: Optional[TimeRange] = None
    entities: List[EntityRef] = field(default_factory=list)
```

### 2.4. Nâng cấp `domain/value_objects/time_range.py`
Thêm factory method:
```python
@classmethod
def last_n_days(cls, n: int = 30) -> "TimeRange":
    from datetime import datetime, timedelta
    end = datetime.utcnow()
    return cls(start=end - timedelta(days=n), end=end)
```

### 2.5. Tạo `application/ports/query/__init__.py` và `query_analyzer.py`
```python
from abc import ABC, abstractmethod
from typing import Optional
from llm_wiki.domain.entities.query_analysis import QueryAnalysis

class QueryAnalyzerPort(ABC):
    @abstractmethod
    async def analyze(self, question: str) -> QueryAnalysis: ...


class QueryRewriterPort(ABC):
    async def rewrite(self, question: str, history: Optional[list[dict]] = None) -> str: ...
```

### 2.6. Nâng cấp `application/ports/search/vector_search.py`
Thêm `time_range: Optional[TimeRange] = None` vào:
- `VectorSearchPort.search_similar`
- `VectorSearchPort.search_sections_similar`
- `VectorSearchPort.search_events_similar`
- `KeywordSearchPort.search_keyword`

### 2.7. Cập nhật `application/dto/query_dto.py`
Thêm optional fields:
```python
@dataclass
class QueryInput:
    question: str
    source_id: Optional[str] = None
    top_k: int = 10
    stream: bool = False
    chat_history: Optional[list[dict]] = None
    # Mới
    intent: Optional[str] = None
    time_range_start: Optional[str] = None
    time_range_end: Optional[str] = None
```

## Success Criteria

- [ ] Tất cả file mới chỉ import từ `domain` và Python stdlib.
- [ ] `QueryIntent` enum có 5 giá trị.
- [ ] `QueryAnalyzerPort.analyze()` được định nghĩa.
- [ ] `VectorSearchPort` và `KeywordSearchPort` có tham số `time_range`.

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Ports breaking existing implementations | High | Thêm `Optional`, default=None |
| DTO thay đổi không tương thích | Low | Thêm field mới với default |
