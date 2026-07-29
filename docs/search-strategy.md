# Search Strategy — Agentic RAG Pipeline

Tài liệu mô tả toàn bộ chiến lược tìm kiếm và sinh câu trả lời khi user đặt câu hỏi trên Chat GUI.

**Ngày phân tích:** 2026-07-29
**Phiên bản:** LLM Wiki backend v2.2.0 / frontend v3.0.1

---

## Tổng quan

Hệ thống có **2 pipeline** song song, chọn dựa vào config `reasoning_enabled`:

| Pipeline | Khi nào dùng | Đặc điểm |
|----------|-------------|---------|
| **Standard** (`QueryPipeline`) | `reasoning_enabled=false` | 1 lần retrieve → generate, cache đơn giản |
| **Reflective** (`SelfReflectiveRAGPipeline`) | `reasoning_enabled=true` (default) | Self-evaluate → retry nếu chất lượng kém, đổi chiến lược |

Route: `POST /api/query` và `POST /api/query/stream` → chọn pipeline dựa trên settings.

`top_k` mặc định: **25**.

---

## Toàn bộ Flow xử lý (12 bước)

```
User đặt câu hỏi trên Chat GUI
         │
         ▼
┌──────────────────────────────────────────────────────┐
│ 1. Cache Check (exact)                               │
│    SHA256 (câu hỏi + scope) → trả ngay nếu hit       │
│    TTL: 1h cho query time-sensitive, 24h cho còn lại │
└────────────────────────┬─────────────────────────────┘
                         │ MISS
                         ▼
┌──────────────────────────────────────────────────────┐
│ 2. Query Rewriting (chỉ khi có chat history)         │
│    LLM phân tích 6 lượt chat gần nhất                │
│    → Giải quyết đại từ, tham chiếu ngầm              │
│    VD: "Thế còn giá vàng hôm qua thì sao?"           │
│        → "Giá vàng ngày 28/07/2026"                  │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 3. Embed câu hỏi → vector                           │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 4. Semantic Cache Check                              │
│    Cosine similarity ≥ 0.80 vs stored embeddings     │
└────────────────────────┬─────────────────────────────┘
                         │ MISS
                         ▼
┌──────────────────────────────────────────────────────┐
│ 5. Query Analysis (LLMQueryAnalyzerAdapter)          │
│    1 lightweight LLM call → tận dụng triệt để:        │
│                                                      │
│    Intent Detection (5 loại):                        │
│    • current_state  — tình hình hiện tại             │
│    • historical     — lịch sử                        │
│    • timeline       — dòng thời gian                 │
│    • comparative    — so sánh                        │
│    • general        — chung chung                    │
│                                                      │
│    Time Range Extraction:                            │
│    • 30+ regex pattern (Vi + En)                     │
│    • LLM fallback nếu regex miss                     │
│                                                      │
│    Entity Extraction:                                │
│    • stock_ticker, commodity, location,              │
│      macro_indicator, person, organization, policy   │
│                                                      │
│    Keyword Extraction (MỚI — dùng cho full-text):     │
│    • keywords: 3-8 từ khóa quan trọng (đã bỏ stopwords)│
│    • key_phrases: 1-3 cụm từ ghép cần match chính xác│
│    • search_query: chuỗi OR-delimited bilingual       │
│      cho PostgreSQL to_tsquery                        │
│      VD: "vàng | gold | giá vàng | kim loại quý"     │
│                                                      │
│    Sub-Questions:                                    │
│    • Nếu câu hỏi phức tạp → 2-4 câu hỏi con          │
│    • Dùng trong decompose strategy (Reflective)       │
│                                                      │
│    Language Detection:                               │
│    • "vi" hoặc "en" — quyết định ngôn ngữ system prompt│
│    • Regex fallback: Vietnamese diacritics → "vi"     │
│      (zero-token, chạy ngay cả khi analyzer lỗi)     │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 6. Multi-Stream Retrieval (5 nguồn, song song)      │
│                                                      │
│   ┌────────────────┐ ┌────────────────┐              │
│   │ PgVector       │ │ TsVector       │              │
│   │ (dense vector) │ │ (sparse keyword)│              │
│   │ cosine distance│ │ ts_rank         │              │
│   │ on page_sections│ │ on page_sections│              │
│   │                │ │ Input: search_query│            │
│   │                │ │ (từ analyzer)    │            │
│   └───────┬────────┘ └───────┬────────┘              │
│           │                  │                        │
│   ┌───────┴──────────────────┴────────┐              │
│   │        Event Search                │              │
│   │  • Dense (pgvector on observations)│              │
│   │  • Sparse (tsvector on observations)│            │
│   │    Input: search_query (từ analyzer)│            │
│   └───────┬────────────────────────────┘              │
│           │                                           │
│   ┌───────┴────────────┐                              │
│   │    Graph RAG        │ (nếu entities được detect) │
│   │ Entity → Event Links│                              │
│   └────────────────────┘                              │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 7. Weighted RRF (Reciprocal Rank Fusion)             │
│                                                      │
│    Trọng số thay đổi theo INTENT:                    │
│                                                      │
│    Intent         Events  Sections  Keyword  Graph   │
│    ─────────      ──────  ────────  ───────  ─────   │
│    current_state  1.0     0.7       0.5/0.3  0.6     │
│    historical     0.8     0.5       0.5/0.3  0.6     │
│    timeline       1.0     0.3       0.5/0.3  0.6     │
│    comparative    0.5     0.8       0.5/0.3  0.6     │
│    general        0.4     1.0       0.5/0.3  0.6     │
│                                                      │
│    Adaptive Recency Decay trong SQL:                 │
│    EXP(-λ * days_old)                                │
│    • current_state: λ=0.05  (~14 ngày half-life)    │
│    • general:       λ=0.01  (~69 ngày)              │
│    • comparative:   λ=0.005 (~138 ngày)             │
│    • historical/timeline: λ=0.0 (không decay)       │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 8. Diversity Capping                                 │
│    • Max 5 results / source                          │
│    • Max 2 results / page                            │
│    • Chọn top 20 đưa vào context                     │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 9. LLM Reranking ⚠️ Chỉ trong Reflective Pipeline    │
│    • LLM chấm điểm từng doc 0-10                     │
│    • Xử lý theo batch 15 docs                        │
│    • Chọn top-K docs tốt nhất                       │
│    • Nếu lỗi → giữ nguyên thứ tự (không fallback)   │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 10. LLM Synthesis (generate answer)                  │
│     • Bilingual system prompt (VI/EN switch theo      │
│       analysis.language)                              │
│     • Current date injection                         │
│     • Intent-specific temporal addendum (VI/EN)       │
│     • Strict date citation rules                     │
│     • Streaming SSE: status → token → complete       │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 11. [Reflective ONLY] Self-Evaluation + Retry        │
│                                                      │
│     LLM đánh giá:                                    │
│     • faithfulness    (0-10, threshold ≥ 7)          │
│     • completeness    (0-10, threshold ≥ 7)          │
│     • relevance       (0-10)                         │
│                                                      │
│     Nếu dưới threshold → Retry với chiến lược mới:   │
│     ┌──────────────────────────────────────────┐    │
│     │ hyde       → Hypothetical Document Embed  │    │
│     │               LLM sinh đoạn văn giả định  │    │
│     │               rồi embed để search         │    │
│     ├──────────────────────────────────────────┤    │
│     │ decompose  → Chia câu hỏi thành nhiều     │    │
│     │               sub-queries, merge kết quả  │    │
│     ├──────────────────────────────────────────┤    │
│     │ expand     → Thêm synonyms vào keyword    │    │
│     │               query để mở rộng tìm kiếm   │    │
│     └──────────────────────────────────────────┘    │
│                                                      │
│     • Max 3 lần retry                                │
│     • Skip rerank nếu top content IDs không đổi      │
│     • Skip evaluate nếu context không đổi            │
│     • Penalty nếu answer từ ≤ 1 distinct page        │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 12. Save Cache (exact + semantic)                    │
└──────────────────────────────────────────────────────┘
```

---

## Chi tiết từng cơ chế

### Query Rewriting

- **Port:** `application/ports/search/query_rewriter_port.py`
- **Adapter:** `infrastructure/llm/query_rewriter_adapter.py`
- **Khi nào chạy:** Chỉ khi có chat history (≥ 1 lượt trước đó)
- **Cơ chế:** LLM phân tích 6 lượt chat gần nhất để resolve pronouns, implicit references
- **Ví dụ:** "Thế còn giá vàng hôm qua thì sao?" → "Giá vàng ngày 28/07/2026"

### Query Analysis (Intent + Entities + Time + Keywords + Language)

- **Port:** `application/ports/search/query_analyzer_port.py`
- **Adapter:** `infrastructure/llm/query_analyzer_adapter.py`
- **1 lightweight LLM call** (`max_tokens=500`, `temperature=0.0`) → output được tận dụng cho nhiều nơi trong pipeline:
- **Output đầy đủ:**
  - **Intent** (5 loại): `current_state`, `historical`, `timeline`, `comparative`, `general` → dùng cho RRF weights, recency decay λ, temporal addendum
  - **Time range**: ngày bắt đầu/kết thúc (regex pattern + LLM fallback) → SQL WHERE filter
  - **Entities**: stock_ticker, commodity, location, macro_indicator, person, organization, policy → Graph RAG traversal
  - **Keywords**: 3-8 từ khóa quan trọng nhất (đã loại bỏ stopwords, bilingual VI+EN) → fallback cho keyword search nếu không có search_query
  - **Key phrases**: 1-3 cụm từ ghép cần match chính xác → dự phòng cho keyword search
  - **Search query**: chuỗi OR-delimited bilingual (`"vàng | gold | giá vàng"`) → input chính cho tsvector `to_tsquery` keyword search và event keyword search
  - **Sub-questions**: 2-4 câu hỏi con nếu câu hỏi phức tạp → dùng trong `decompose` strategy (Reflective), tránh gọi LLM thêm lần nữa
  - **Language**: `"vi"` hoặc `"en"` → quyết định ngôn ngữ system prompt khi synthesize, `_temporal_addendum()`, và system prompt trong Reflective pipeline

### Keyword Extraction & Language Detection

**Keyword extraction** nằm trong cùng 1 LLM call của Query Analysis — không cần thêm LLM call riêng. Prompt yêu cầu LLM:
- Loại bỏ stopwords tiếng Việt (cho, tôi, biết, những, nào, về, trong, là, có, các, và, của, được, không, để, với, sẽ, ra, này, đã, đang, từ, …)
- Giữ lại: danh từ riêng, thuật ngữ chuyên ngành, số liệu, địa danh, tên tổ chức
- Nếu câu hỏi tiếng Việt → thêm bản tiếng Anh của thuật ngữ để search được cả nội dung EN
- Output `search_query` dùng `|` (OR logic) cho `to_tsquery` — khác với `plainto_tsquery` mặc định (AND logic) dễ bị 0 match khi query dài

**Language detection** có 2 tầng:
1. **Primary:** Analyzer LLM output `language` field — chính xác, không tốn thêm token
2. **Fallback:** Regex `_VI_DIACRITICS` (Vietnamese diacritics) trong `pipeline.py` — zero-token, chạy ngay cả khi analyzer lỗi fallback về `intent="general"`
   ```python
   _VI_DIACRITICS = re.compile(r'[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôồốỗổộơớờởỡợùúủũụưứừửữựỳýỷỹỵ]')
   language = analysis.language or _detect_language(question)
   ```

**Bilingual system prompt** — không tốn thêm LLM call:
- `pipeline.py`: `_temporal_addendum(intent, language)` có EN/VI variants
- `reflective_pipeline.py`: `_INTENT_EN_PROMPTS` dict chứa English variants cho 5 intents; fallback về Vietnamese prompts
- Switch dựa trên `analysis.language` → zero additional tokens

### Query Expansion

- **Port:** `application/ports/search/query_expander_port.py`
- **Adapter:** `infrastructure/llm/query_expander_adapter.py`
- **Cơ chế:** LLM sinh 3-5 synonym/cách diễn đạt khác bằng tiếng Việt
- **Ví dụ:** "bất động sản" → "nhà đất", "địa ốc"
- **Dùng trong:** keyword search và chiến lược retry `expand`

### Multi-Stream Hybrid Search

| # | Stream | Công nghệ | Đối tượng | Input | Trường |
|---|--------|----------|-----------|-------|--------|
| 1 | Dense vector | pgvector (cosine distance) | `page_sections` | Question embedding | `section_vector` |
| 2 | Sparse keyword | tsvector (`to_tsquery`, `ts_rank`) | `page_sections` | `analysis.search_query` (OR-delimited, bilingual) | `fts_vector` |
| 3 | Event dense | pgvector | `event_observations` | Question embedding | `embedding` |
| 4 | Event keyword | tsvector | `event_observations` | `analysis.search_query` (OR-delimited, bilingual) | `fts_vector` |
| 5 | Graph traversal | Entity→Event→Observation | `event_entity_links` | Detected entities | — |

Tất cả 5 stream chạy **song song**. Graph RAG chỉ kích hoạt nếu entity được detect ở bước Query Analysis.

**Keyword query logic** (`_build_keyword_query()`):
1. Nếu analyzer trả về `search_query` → dùng trực tiếp (OR logic, `to_tsquery`)
2. Nếu chỉ có `keywords` → join với ` | ` (OR logic)
3. Fallback: raw question → `plainto_tsquery` (AND logic, hành vi cũ)

Việc dùng `search_query` từ analyzer thay vì raw question giúp:
- Loại bỏ stopwords gây 0 match
- OR logic tăng recall (so với AND của `plainto_tsquery`)
- Bilingual terms search được cả nội dung tiếng Việt và tiếng Anh

### Weighted RRF (Reciprocal Rank Fusion)

Công thức RRF: `score = Σ(weight / (k + rank))` với `k=60`.

**Trọng số theo intent** — đây là cơ chế chính để query sát nghĩa:

| Intent | Events (dense) | Sections (dense) | Keyword sections | Keyword events | Graph |
|--------|:-:|:-:|:-:|:-:|:-:|
| `current_state` | **1.0** | 0.7 | 0.5 | 0.3 | 0.6 |
| `historical` | 0.8 | 0.5 | 0.5 | 0.3 | 0.6 |
| `timeline` | **1.0** | 0.3 | 0.5 | 0.3 | 0.6 |
| `comparative` | 0.5 | 0.8 | 0.5 | 0.3 | 0.6 |
| `general` | 0.4 | **1.0** | 0.5 | 0.3 | 0.6 |

### Adaptive Recency Decay

Decay được áp dụng trực tiếp trong SQL query: `score * EXP(-λ * days_old)`

| Intent | λ | Half-life | Ý nghĩa |
|--------|---|-----------|---------|
| `current_state` | 0.05 | ~14 ngày | Tin cũ hơn 2 tuần bị giảm 50% trọng số |
| `general` | 0.01 | ~69 ngày | Tin cũ giảm nhẹ |
| `comparative` | 0.005 | ~138 ngày | Giảm rất ít |
| `historical` / `timeline` | 0.0 | ∞ | Không decay — tin lịch sử vẫn giá trị |

### Diversity Capping

- Max **5 results / source** (tránh 1 nguồn chi phối)
- Max **2 results / page** (tránh 1 trang chi phối)
- Lấy top **20 results** đưa vào LLM context

### Reranking (LLM-based)

- **Port:** `application/ports/search/reranker_port.py`
- **Adapter:** `infrastructure/llm/reranker_adapter.py`
- **Cơ chế:** LLM (OpenCode/Gemini) chấm điểm relevance 0-10 cho từng doc
- **Batch size:** 15 docs
- **⚠️ Chỉ chạy trong Reflective Pipeline** — Standard pipeline không có rerank
- **Không có cross-encoder fallback** — nếu LLM reranker lỗi thì giữ nguyên thứ tự
- **Smart skip:** bỏ qua rerank nếu top content IDs không thay đổi sau retry

### Self-Reflective Retry Loop

Chỉ có trong `SelfReflectiveRAGPipeline` (`reasoning_enabled=true`):

1. **LLM-as-Judge** đánh giá 3 tiêu chí: faithfulness, completeness, relevance (0-10)
2. **Threshold:** faithfulness ≥ 7, completeness ≥ 7 → pass
3. **Nếu fail** → retry với chiến lược mới:

| Chiến lược | Cơ chế |
|-----------|--------|
| `hyde` | LLM sinh hypothetical document, embed để search (thay vì embed câu hỏi) |
| `decompose` | Dùng `analysis.sub_questions` từ analyzer (nếu có) hoặc LLM decompose. Không gọi LLM thêm nếu analyzer đã phân rã sẵn. |
| `expand` | Dùng Query Expander để thêm synonyms vào keyword search |

4. **Hard limit:** tối đa **3 lần retry**
5. **Source diversity penalty:** trừ điểm nếu answer chỉ từ ≤ 1 distinct page
6. **Streaming behavior:**
   - Lần 1 stream live (user thấy real-time)
   - Lần 2-3 chạy ngầm, emit `complete` event mới khi xong → user thấy câu trả lời "nhảy"

---

## Caching

### Exact Cache

- **Key:** SHA256 (`question` + optional `source` scope)
- **TTL:** 1 giờ cho time-sensitive queries, 24 giờ cho còn lại
- **Check:** ở đầu pipeline (bước 1)

### Semantic Cache

- **Key:** question embedding vector
- **Match:** cosine similarity ≥ **0.80**
- **Check:** sau khi embed (bước 4)

---

## Các file chính

| Layer | File | Vai trò |
|-------|------|---------|
| Entrypoint | `main.py` | FastAPI app, register routes |
| Route | `presentation/routes/query.py` | Build adapters, chọn pipeline, xử lý request |
| Pipeline | `application/use_cases/query/pipeline.py` | Standard pipeline (QueryPipeline) |
| Pipeline | `application/use_cases/query/reflective_pipeline.py` | Reflective pipeline (SelfReflectiveRAGPipeline) |
| Config | `config.py` | Settings: reasoning_enabled, reranker_enabled, etc. |
| Frontend | `frontend/hooks/use-query-stream.ts` | SSE client, 180s timeout |
| **Ports** | | |
| Query Analyzer | `application/ports/search/query_analyzer_port.py` | Interface: intent, time_range, entities, keywords, key_phrases, search_query, sub_questions, language |
| Query Rewriter | `application/ports/search/query_rewriter_port.py` | Interface: resolve pronouns |
| Query Expander | `application/ports/search/query_expander_port.py` | Interface: sinh synonyms |
| Reranker | `application/ports/search/reranker_port.py` | Interface: rerank docs |
| Graph RAG | `application/ports/search/graph_rag_port.py` | Interface: graph traversal |
| Answer Evaluator | `application/ports/search/answer_evaluator_port.py` | Interface: evaluate answer quality |
| Event Search | `application/ports/search/event_search_port.py` | Interface: dense + sparse event search |
| **Adapters** | | |
| LLM Analyzer | `infrastructure/llm/query_analyzer_adapter.py` | LLM-based: intent, time_range, entities, keywords, key_phrases, search_query, sub_questions, language |
| LLM Rewriter | `infrastructure/llm/query_rewriter_adapter.py` | LLM-based pronoun resolution |
| LLM Expander | `infrastructure/llm/query_expander_adapter.py` | LLM-based synonym generation |
| LLM Reranker | `infrastructure/llm/reranker_adapter.py` | LLM-based doc scoring |
| LLM Evaluator | `infrastructure/llm/answer_evaluator_adapter.py` | LLM-as-judge |
| Graph RAG | `infrastructure/search/graph_rag_adapter.py` | PostgreSQL graph traversal |
| PgVector | `infrastructure/search/pgvector_adapter.py` | Dense vector search |
| TsVector | `infrastructure/search/tsvector_adapter.py` | Sparse keyword search |
| Event Search | `infrastructure/search/event_search_adapter.py` | Dense + sparse event search |
| **Traced Wrappers** | | |
| Traced LLM | `infrastructure/llm/traced_llm_wrapper.py` | Spans for all LLM calls |
| Traced Analyzer | `infrastructure/llm/traced_query_analyzer_wrapper.py` | Spans for query_analyze with keywords, language, sub_questions |
| Traced Event Search | `infrastructure/search/traced_event_search_wrapper.py` | Spans for event_search + event_keyword_search |

---

## Những điểm cần lưu ý

1. **`reranker_enabled` và `temporal_precision_enabled`** trong `config.py` được load nhưng không được pipeline đọc — config flag chết.
2. **Reranker chỉ có trong Reflective mode** — Standard `QueryPipeline` không rerank, chỉ RRF fusion rồi đưa thẳng vào LLM.
3. **Reranker dùng LLM, không phải cross-encoder** — có thể chậm và đắt hơn so với model chuyên dụng (BGE-reranker, Cohere Rerank).
4. **`traverse_timeline` chưa được wired** — Graph RAG port có định nghĩa `traverse_timeline()` để duyệt causal/temporal chains giữa events nhưng chưa pipeline nào gọi.
5. **Không có fallback reranker** — nếu LLMRerankerAdapter lỗi, kết quả giữ nguyên thứ tự RRF.
6. **Reflective streaming UX** — retry 2-3 chạy ngầm và thay thế `complete` event; frontend hiện tại overwrite `answer` nếu `payload.answer` có giá trị.
