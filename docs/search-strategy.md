# Search Strategy — Agentic RAG Pipeline

Tài liệu mô tả toàn bộ chiến lược tìm kiếm và sinh câu trả lời khi user đặt câu hỏi trên Chat GUI.

**Ngày cập nhật:** 2026-08-05
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

## Toàn bộ Flow xử lý (11 bước)

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
│ 2. Embed câu hỏi → vector                           │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 3. Semantic Cache Check                              │
│    Cosine similarity ≥ 0.80 vs stored embeddings     │
└────────────────────────┬─────────────────────────────┘
                         │ MISS
                         ▼
┌──────────────────────────────────────────────────────┐
│ 4. Guardrail + Intent Analysis                       │
│    (LLMGuardrailAnalyzerAdapter — 1 LLM call)        │
│                                                      │
│    0. Guardrail: Domain check (kinh tế/tài chính)    │
│       • allowed=true → tiếp tục                      │
│       • allowed=false → trả về lý do từ chối         │
│                                                      │
│    1. Intent Detection (6 loại):                     │
│    • current_state  — tình hình hiện tại             │
│    • historical     — mốc thời gian cụ thể           │
│    • timeline       — diễn biến theo thời gian       │
│    • comparative    — so sánh                        │
│    • factual_listing — liệt kê, danh sách            │
│    • general        — chung chung                    │
│                                                      │
│    2. Time Range Extraction:                         │
│    • LLM phân tích dựa trên ngày hôm nay             │
│    • Prompt injects: "Hôm nay là YYYY-MM-DD"         │
│    • Ngăn LLM hallucinate sai năm (VD: 2025 thay vì 2026) │
│                                                      │
│    3. Entity Extraction:                             │
│    • stock_ticker, commodity, location,              │
│      macro_indicator, person, organization, policy   │
│                                                      │
│    4. Per-Tool Search Inputs (MỚI — riêng cho từng tool):│
│    • embedding_text: structured format               │
│      "query: <câu semantic cho bge-m3>                │
│       keywords: <từ khóa VI> | <từ khóa EN>"         │
│      → Dùng chung cho vector_search & event_search   │
│    • page_search_query: OR-delimited bilingual       │
│      → Dùng cho keyword_search trên page_sections    │
│      Tập trung thuật ngữ chuyên ngành, phân tích     │
│    • event_search_query: OR-delimited bilingual      │
│      → Dùng cho event_keyword_search                 │
│      Tập trung tên riêng, sự kiện cụ thể, số liệu    │
│                                                      │
│    5. Sub-Questions:                                 │
│    • Nếu câu hỏi phức tạp → 2-4 câu hỏi con          │
│    • Dùng trong decompose strategy (Reflective)       │
│                                                      │
│    6. Language Detection:                            │
│    • "vi" hoặc "en" — quyết định ngôn ngữ system prompt│
│    • Regex fallback: Vietnamese diacritics → "vi"     │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 5. Multi-Stream Retrieval (4 nguồn, song song)      │
│                                                      │
│   ┌────────────────┐ ┌────────────────┐              │
│   │ PgVector       │ │ TsVector       │              │
│   │ (dense vector) │ │ (sparse keyword)│              │
│   │ cosine distance│ │ ts_rank         │              │
│   │ on page_sections│ │ on page_sections│              │
│   │                │ │ Input: page_    │              │
│   │ Input:         │ │ search_query    │              │
│   │ embedding_text │ │ (từ analyzer)   │              │
│   │ (từ analyzer)  │ │                │              │
│   └───────┬────────┘ └───────┬────────┘              │
│           │                  │                        │
│   ┌───────┴──────────────────┴────────┐              │
│   │        Event Search                │              │
│   │  • Dense (pgvector on observations)│              │
│   │    Input: embedding_text           │              │
│   │    (dùng chung với vector search)  │              │
│   │  • Sparse (tsvector on observations)│            │
│   │    Input: event_search_query       │              │
│   └───────┬────────────────────────────┘              │
│           │                                           │
│   ┌───────┴────────────┐                              │
│   │    Graph RAG        │ (nếu entities được detect) │
│   │ Entity → Event Links│                              │
│   └────────────────────┘                              │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 6. Weighted RRF (Reciprocal Rank Fusion)             │
│    ... (weights by intent, same as before)           │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 7. Diversity Capping                                 │
│    • Max 5 results / source                          │
│    • Max 2 results / page                            │
│    • Chọn top 20 đưa vào context                     │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 8. LLM Reranking ⚠️ Chỉ trong Reflective Pipeline    │
│    • LLM chấm điểm từng doc 0-10                     │
│    • Xử lý theo batch 15 docs                        │
│    • Chọn top-K docs tốt nhất                       │
│    • Nếu lỗi → giữ nguyên thứ tự (không fallback)   │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 9. LLM Synthesis (generate answer)                  │
│     • Bilingual system prompt (VI/EN switch theo      │
│       analysis.language)                              │
│     • Current date injection                         │
│     • Intent-specific temporal addendum (VI/EN)       │
│     • Strict date citation rules                     │
│     • Streaming SSE: status → token → complete       │
│     • Full input/output visible in LangSmith          │
│       (_redacted_messages now stores full content)    │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 10. [Reflective ONLY] Self-Evaluation + Retry        │
│     (same as before)                                 │
└────────────────────────┬─────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│ 11. Save Cache (exact + semantic)                    │
└──────────────────────────────────────────────────────┘
```

---

## Chi tiết từng cơ chế

### Query Rewriting

- **⚠️ DEPRECATED — đã được merge vào Guardrail Analyzer.**
- **Port:** `application/ports/search/query_rewriter_port.py` (legacy, kept for backward compat)
- **Khi nào chạy:** Không còn được wired trong pipeline. Pronoun resolution trước đây chạy riêng nhưng đã được gộp vào prompt của `GuardrailAnalyzerPort`.
- Pipeline hiện tại không dùng query rewriting — mỗi câu hỏi là độc lập, không có chat history context.

### Guardrail + Intent Analysis (Unified — 1 LLM call)

- **Port:** `application/ports/search/guardrail_analyzer_port.py`
- **Adapter:** `infrastructure/llm/guardrail_analyzer_adapter.py`
- **1 lightweight LLM call** (`max_tokens=600`, `temperature=0.0`) thay thế old rewrite→analyze chain:
- **Output đầy đủ:**
  - **Guardrail**: `allowed=true/false` — domain check (kinh tế/tài chính), `reason` nếu bị từ chối
  - **Intent** (6 loại): `current_state`, `historical`, `timeline`, `comparative`, `factual_listing`, `general`
  - **Time range**: ngày bắt đầu/kết thúc — LLM phân tích dựa trên ngày hôm nay được inject vào prompt
  - **Entities**: stock_ticker, commodity, location, macro_indicator, person, organization, policy
  - **embedding_text**: Structured format `"query: <câu semantic> keywords: <từ khóa VI> | <từ khóa EN>"` — dùng chung cho vector_search & event_search. bge-m3 dùng phần "query" để match meaning, phần "keywords" để tăng recall
  - **page_search_query**: OR-delimited bilingual — input cho tsvector keyword_search trên page_sections. Tập trung thuật ngữ chuyên ngành
  - **event_search_query**: OR-delimited bilingual — input cho tsvector event_keyword_search trên event_observations. Tập trung tên riêng, sự kiện cụ thể
  - **Sub-questions**: 2-4 câu hỏi con nếu câu hỏi phức tạp → dùng trong `decompose` strategy
  - **Language**: `"vi"` hoặc `"en"` → quyết định ngôn ngữ system prompt

### Keyword Extraction & Per-Tool Search Inputs

**Thay đổi quan trọng:** Không còn generic keyword blob nữa. Analyzer giờ tạo **3 input riêng biệt** cho 3 loại công cụ tìm kiếm:

1. **embedding_text** (dùng cho vector_search + event_search):
   - Structured format: `query: ... keywords: ...`
   - Phần "query": câu semantic chính để bge-m3 match meaning
   - Phần "keywords": từ khóa bổ trợ VI+EN, chỉ liệt kê, không viết thành câu
   - Ví dụ: `"query: giá vàng hôm nay cập nhật mới nhất biến động keywords: vàng | gold price | XAU USD"`

2. **page_search_query** (dùng cho keyword_search trên page_sections):
   - OR-delimited (`|`), tập trung thuật ngữ chuyên ngành, khái niệm phân tích
   - Ví dụ: `"ngân hàng | bank | lãi suất | tín dụng | room tín dụng | phân tích ngành"`

3. **event_search_query** (dùng cho event_keyword_search trên event_observations):
   - OR-delimited (`|`), tập trung tên riêng, sự kiện cụ thể, số liệu
   - Ví dụ: `"VCB | BID | CTG | tăng lãi suất | room tín dụng | NHNN | tăng vốn"`

**Language detection** có 2 tầng:
1. **Primary:** Analyzer LLM output `language` field
2. **Fallback:** Regex `_VI_DIACRITICS` (Vietnamese diacritics) trong `pipeline.py`

**Today's date injection:** Prompt injects `"Hôm nay là {YYYY-MM-DD}"` để ngăn LLM (đặc biệt là deepseek-v4-flash) hallucinate sai năm.

### Query Expansion

- **Port:** `application/ports/search/query_expander_port.py`
- **Adapter:** `infrastructure/llm/query_expander_adapter.py`
- **Cơ chế:** LLM sinh 3-5 synonym/cách diễn đạt khác bằng tiếng Việt
- **Ví dụ:** "bất động sản" → "nhà đất", "địa ốc"
- **Dùng trong:** keyword search và chiến lược retry `expand`

### Multi-Stream Hybrid Search

| # | Stream | Công nghệ | Đối tượng | Input | Trường |
|---|--------|----------|-----------|-------|--------|
| 1 | Dense vector | pgvector (cosine distance) | `page_sections` | `analysis.embedding_text` (structured bilingual) | `section_vector` |
| 2 | Sparse keyword | tsvector (`to_tsquery`, `ts_rank`) | `page_sections` | `analysis.page_search_query` (OR-delimited, domain terms) | `fts_vector` |
| 3 | Event dense | pgvector | `event_observations` | `analysis.embedding_text` (shared with stream 1) | `embedding` |
| 4 | Event keyword | tsvector | `event_observations` | `analysis.event_search_query` (OR-delimited, proper nouns) | `fts_vector` |
| 5 | Graph traversal | Entity→Event→Observation | `event_entity_links` | Detected entities | — |

Tất cả 5 stream chạy **song song**. Graph RAG chỉ kích hoạt nếu entity được detect.

**Key change from old design:** Stream 1 & 3 share `embedding_text` (không còn embed raw question nữa). Stream 2 dùng `page_search_query` riêng, stream 4 dùng `event_search_query` riêng — mỗi tool có input tối ưu cho loại dữ liệu nó tìm kiếm.

### Weighted RRF (Reciprocal Rank Fusion)

Công thức RRF: `score = Σ(weight / (k + rank))` với `k=60`.

**Trọng số theo intent** — đây là cơ chế chính để query sát nghĩa:

| Intent | Events (dense) | Sections (dense) | Keyword sections | Keyword events | Graph |
|--------|:-:|:-:|:-:|:-:|:-:|
| `current_state` | **1.0** | 0.7 | 0.5 | 0.3 | 0.6 |
| `historical` | 0.8 | 0.5 | 0.5 | 0.3 | 0.6 |
| `timeline` | **1.0** | 0.3 | 0.5 | 0.3 | 0.6 |
| `comparative` | 0.5 | 0.8 | 0.5 | 0.3 | 0.6 |
| `factual_listing` | 0.3 | **1.0** | 0.5 | 0.3 | 0.6 |
| `general` | 0.4 | **1.0** | 0.5 | 0.3 | 0.6 |

### Adaptive Recency Decay

Decay được áp dụng trực tiếp trong SQL query: `score * EXP(-λ * days_old)`

| Intent | λ | Half-life | Ý nghĩa |
|--------|---|-----------|---------|
| `current_state` | 0.05 | ~14 ngày | Tin cũ hơn 2 tuần bị giảm 50% trọng số |
| `general` / `factual_listing` | 0.01 | ~69 ngày | Tin cũ giảm nhẹ |
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
| Guardrail Analyzer | `application/ports/search/guardrail_analyzer_port.py` | **ACTIVE** — unified guardrail + intent + per-tool search inputs |
| Query Analyzer | `application/ports/search/query_analyzer_port.py` | LEGACY — superseded by guardrail_analyzer_port |
| Query Rewriter | `application/ports/search/query_rewriter_port.py` | LEGACY — not wired; kept for backward compat |
| Query Expander | `application/ports/search/query_expander_port.py` | Interface: sinh synonyms (used in reflective pipeline expand strategy) |
| Reranker | `application/ports/search/reranker_port.py` | Interface: rerank docs |
| Graph RAG | `application/ports/search/graph_rag_port.py` | Interface: graph traversal |
| Answer Evaluator | `application/ports/search/answer_evaluator_port.py` | Interface: evaluate answer quality |
| Event Search | `application/ports/search/event_search_port.py` | Interface: dense + sparse event search |
| **Adapters** | | |
| Guardrail Analyzer | `infrastructure/llm/guardrail_analyzer_adapter.py` | **ACTIVE** — unified guardrail+intent+per-tool inputs. Injects today's date |
| LLM Analyzer | `infrastructure/llm/query_analyzer_adapter.py` | LEGACY — superseded |
| LLM Rewriter | `infrastructure/llm/query_rewriter_adapter.py` | LEGACY — not wired |
| LLM Expander | `infrastructure/llm/query_expander_adapter.py` | LLM-based synonym generation |
| LLM Reranker | `infrastructure/llm/reranker_adapter.py` | LLM-based doc scoring |
| LLM Evaluator | `infrastructure/llm/answer_evaluator_adapter.py` | LLM-as-judge |
| Graph RAG | `infrastructure/search/graph_rag_adapter.py` | PostgreSQL graph traversal |
| PgVector | `infrastructure/search/pgvector_adapter.py` | Dense vector search |
| TsVector | `infrastructure/search/tsvector_adapter.py` | Sparse keyword search |
| Event Search | `infrastructure/search/event_search_adapter.py` | Dense + sparse event search |
| **Traced Wrappers** | | |
| Traced LLM | `infrastructure/llm/traced_llm_wrapper.py` | Spans for all LLM calls (full content, no redaction) |
| Traced Guardrail Analyzer | `infrastructure/llm/traced_guardrail_analyzer_wrapper.py` | Spans for guardrail_analyze with per-tool inputs |
| Traced Analyzer | `infrastructure/llm/traced_query_analyzer_wrapper.py` | LEGACY — superseded |
| Traced Event Search | `infrastructure/search/traced_event_search_wrapper.py` | Spans for event_search + event_keyword_search |

---

## Những điểm cần lưu ý

1. **`reranker_enabled` và `temporal_precision_enabled`** trong `config.py` được load nhưng không được pipeline đọc — config flag chết.
2. **Reranker chỉ có trong Reflective mode** — Standard `QueryPipeline` không rerank, chỉ RRF fusion rồi đưa thẳng vào LLM.
3. **Reranker dùng LLM, không phải cross-encoder** — có thể chậm và đắt hơn so với model chuyên dụng (BGE-reranker, Cohere Rerank). Có thể bật Cross-Encoder qua `CROSS_ENCODER_ENABLED=true`.
4. **`traverse_timeline` chưa được wired** — Graph RAG port có định nghĩa `traverse_timeline()` để duyệt causal/temporal chains giữa events nhưng chưa pipeline nào gọi.
5. **Không có fallback reranker** — nếu LLMRerankerAdapter lỗi, kết quả giữ nguyên thứ tự RRF.
6. **Reflective streaming UX** — retry 2-3 chạy ngầm và thay thế `complete` event; frontend hiện tại overwrite `answer` nếu `payload.answer` có giá trị.
7. **Legacy ports vẫn tồn tại** — `QueryAnalyzerPort`, `QueryRewriterPort` và adapters/traced wrappers của chúng vẫn trong codebase nhưng không được wired. Pipeline hiện tại chỉ dùng `GuardrailAnalyzerPort`.
8. **GuardrailAnalyzerAdapter injects today's date** — prompt có `f"Hôm nay là {now().strftime('%Y-%m-%d')}"` để ngăn deepseek-v4-flash hallucinate sai năm (VD: 2025 thay vì 2026).
9. **Embedding text dùng structured format** — `"query: ... keywords: ..."` thay vì mixed VI+EN natural language trước đây. Phần query giúp bge-m3 match meaning, phần keywords giúp tăng recall cross-lingual.
