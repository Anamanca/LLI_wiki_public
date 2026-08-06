# Telemetry Implementation Strategy

> **Last updated:** 2026-07-30
> **Status:** Implemented & deployed
> **Observability platform:** LangSmith (by LangChain)

---

## 1. Overview

The LLM Wiki telemetry system tracks every execution path through the system — from user queries down to individual LLM API calls and embedding operations — so developers and interviewers can:

1. **Trace** a single question from cache check through retrieval to answer synthesis.
2. **Inspect** the ingestion pipeline: transcript → classify → 3-pass wiki integrate → section embedding.
3. **Measure** latency, token usage, and error counts at every layer.
4. **Debug** failures by walking the full parent-child span tree.

All spans surface in the LangSmith web UI (`https://smith.langchain.com`) as a **single hierarchical trace tree** — not isolated root runs.

### Why this matters

The trace tree **proves** the system architecture is real. An interviewer can:

- Open any `rag_query` trace and see the entire pipeline in one view.
- Confirm the 3-tier cache (exact → semantic → LLM synthesis) is implemented and working.
- Verify the 3-pass wiki integrator (Pass 1 extract → Pass 2 analyze → Pass 3 write) is structured correctly.
- Check latency breakdown: how much time is spent in embedding vs. retrieval vs. synthesis.
- Confirm zero-cost cache hits skip the LLM entirely.

---

## 2. Architecture: Ports & Adapters Pattern

The telemetry system follows the same Clean Architecture pattern as the rest of the codebase.

```
┌─────────────────────────────────────────────────────────┐
│  application/ports/telemetry/telemetry_port.py           │
│  ┌─────────────────────────┐                            │
│  │ TelemetryPort (ABC)     │  ← Abstract contract        │
│  │  + start_span()         │                            │
│  │  + end_span()           │                            │
│  │  + add_metadata()       │                            │
│  └─────────────────────────┘                            │
└──────────────────┬──────────────────────────────────────┘
                   │ implements
    ┌──────────────┴──────────────┐
    │                             │
    ▼                             ▼
┌───────────────────┐   ┌───────────────────┐
│ LangSmithAdapter  │   │ NullAdapter       │
│ (production)      │   │ (LANGSMITH_TRACING│
│                   │   │  =false / no key) │
│ parent_run.       │   │ no-op: returns    │
│ create_child()    │   │ empty spans,      │
│ → proper tree     │   │ pipeline runs     │
│                   │   │ normally          │
└───────────────────┘   └───────────────────┘
```

### 2.1 `TelemetryPort` — the abstract contract

```python
# application/ports/telemetry/telemetry_port.py
class TelemetryPort(ABC):
    async def start_span(name, kind, inputs, metadata, parent) -> TelemetrySpan
    async def end_span(span, outputs, error) -> None
    async def add_metadata(span, metadata) -> None
```

- `start_span` creates a **child span** when `parent` is provided, or a **root trace** when `parent=None`.
- `end_span` closes the span, optionally recording outputs or an error message.
- `add_metadata` attaches extra key-value data mid-span (latency, token counts, etc.).

### 2.2 `LangSmithTelemetryAdapter` — the real tracer

**File:** `infrastructure/telemetry/langsmith_telemetry_adapter.py`

Uses the LangSmith SDK `RunTree` API. The critical design decision is **how parent-child linking works:**

```python
# Before (broken — creates isolated root runs):
run = self._RunTree(name=name, ..., parent=parent_run)

# After (fixed — proper parent-child in same trace):
if parent_run is not None:
    run = parent_run.create_child(name=name, run_type=kind, ...)
else:
    run = self._RunTree(name=name, ...)
```

**Why `create_child()` is essential:** `RunTree(parent=...)` creates a new root trace that references the parent by ID but appears **isolated** in the LangSmith UI — you see 261 flat runs instead of one tree. `create_child()` links the new run into the parent's trace DNA, producing **a single hierarchical tree**.

**About `span_id` — internal, nothing to configure:** The adapter maintains two `contextvars.ContextVar` for log correlation:

| Var | Source | Purpose |
|-----|--------|---------|
| `_current_trace_id` | LangSmith `run.id` (returned by `run.post()`) | Correlate logs → LangSmith trace |
| `_current_span_id` | `uuid4()` auto-generated in `start_span()` | Correlate logs → specific span within a trace |

Both are **internal implementation details** — auto-generated at runtime, injected into log records by `TraceIdFilter`. There is **no env var, no config field, nothing to declare**. They "just work."

### 2.3 `NullTelemetryAdapter` — graceful degradation

When `LANGSMITH_TRACING=false` or the API key is missing, the adapter factory returns a no-op implementation. Every call succeeds silently — the pipeline runs identically, just without observability data.

### 2.4 Factory

```python
# infrastructure/telemetry/__init__.py
def create_telemetry_adapter(...) -> TelemetryPort:
    if not enabled or not api_key:
        return NullTelemetryAdapter()
    try:
        return LangSmithTelemetryAdapter(api_key, api_url, project_name)
    except Exception:
        logger.warning("LangSmith init failed, falling back to no-op")
        return NullTelemetryAdapter()
```

---

## 3. Traced Wrappers — Span Emission Without Changing Business Logic

Every external service is wrapped in a **traced proxy** that implements the same port interface. The wrapper emits spans before/after each call and delegates to the real adapter.

| Wrapper | Wraps Interface | Emits Spans For |
|---------|----------------|-----------------|
| `TracedLLMWrapper` | `LLMClientPort` | `chat_completion`, `chat_completion_raw`, `chat_completion_reasoning`, `chat_completion_stream` — **full message content stored** (no redaction) |
| `TracedGuardrailAnalyzerWrapper` | `GuardrailAnalyzerPort` | `guardrail_analyze` — allowed, intent, language, embedding_text preview, page_search_query, event_search_query, sub_questions, entities, time_range |
| `TracedEmbeddingWrapper` | `EmbeddingServicePort` | `embed_query`, `embed_documents` |
| `TracedVectorSearchWrapper` | `VectorSearchPort` | `vector_search` |
| `TracedKeywordSearchWrapper` | `KeywordSearchPort` | `keyword_search` |
| `TracedEventSearchWrapper` | `EventSearchPort` | `event_search` (dense vector), `event_keyword_search` (sparse keyword with query text) |
| `TracedCacheWrapper` | `CacheServicePort` | `cache_get`, `cache_set` |

### Pattern (simplified from `TracedLLMWrapper`)

```python
class TracedLLMWrapper(LLMClientPort):
    def __init__(self, inner, telemetry, model, parent_span=None):
        self._inner = inner
        self._telemetry = telemetry
        self._parent_span = parent_span

    def set_parent_span(self, parent: TelemetrySpan):
        """Wire this wrapper under a parent span for proper tree nesting."""
        self._parent_span = parent

    async def chat_completion(self, messages, ...) -> str:
        span = await self._telemetry.start_span(
            name="llm_chat_completion", kind="llm",
            inputs={"model": self._model, ...},
            parent=self._parent_span,   # ← nest under parent
        )
        try:
            answer = await self._inner.chat_completion(messages, ...)
            await self._telemetry.end_span(span, outputs={"answer_length": len(answer)})
            return answer
        except Exception as exc:
            await self._telemetry.end_span(span, error=str(exc))
            raise
```

**Key insight:** The business logic (`QueryPipeline`, `WikiIntegrator`) calls `llm.chat_completion(...)` without knowing the call is traced. The wrapper intercepts transparently.

---

## 4. Trace Tree: Query Pipeline (`/api/query`)

### 4.1 Span hierarchy in LangSmith

```
rag_query (chain)                              ← root: created in query.py
├── cache_check (chain)                        ← exact + semantic cache lookup
│   ├── cache_get (cache)                      ← Redis exact-match check
│   ├── embedding (embedding)                  ← embed question for semantic cache
│   └── cache_semantic_get (cache)             ← semantic cache lookup
├── guardrail_analyze (chain)                  ← unified guardrail + intent + per-tool inputs
│   │  inputs: question (full text), today's date injected
│   │  outputs: allowed, intent, language, time_range,
│   │           entities_count, sub_questions_count,
│   │           embedding_text (truncated 200 chars),
│   │           page_search_query, event_search_query
│   │  metadata: entities[], sub_questions[], latency_ms
├── embedding (embedding)                      ← embed embedding_text for search
├── vector_search (retriever)                  ← pgvector HNSW cosine + time filter + recency
│   │  inputs: embedding_dimensions (1024), time_range, top_k
├── keyword_search (retriever)                 ← tsvector full-text + time filter + recency
│   │  inputs: query (analyzer page_search_query)
├── event_search (retriever)                   ← pgvector on event_observations
│   │  inputs: embedding_dimensions, top_k, time_range
├── event_keyword_search (retriever)           ← tsvector on event_observations
│   │  inputs: query (analyzer event_search_query), top_k, time_range
├── rerank (chain)                             ← RRF merge + LLM rerank (if enabled)
│   └── llm_chat_completion_reasoning (llm)    ← reasoning model scores candidates
└── llm_chat_completion_stream (llm)           ← final answer synthesis
```

### 4.2 What to look for in LangSmith

| Signal | Where to check |
|--------|---------------|
| Cache hit rate | `cache_get` → check outputs for `{hit: true}` vs `{hit: false}` |
| Guardrail analysis quality | `guardrail_analyze` → outputs: allowed, intent, language; metadata: embedding_text (preview), page_search_query, event_search_query, sub_questions[] |
| Embedding latency | `embedding` span → metadata has `latency_ms` |
| Search recall | `vector_search`, `keyword_search`, `event_search`, `event_keyword_search` → `outputs.result_count` |
| Per-tool search inputs | `keyword_search` → `inputs.query` (page_search_query), `event_keyword_search` → `inputs.query` (event_search_query) |
| LLM synthesis input | `llm_chat_completion_stream` → `inputs.messages[]` (full content visible, no redaction) |
| Token usage | Each `llm_*` span → `metadata.tokens_used` |
| Total pipeline latency | `rag_query` root span → wall-clock start to end |
| Rerank effectiveness | `rerank` → `outputs.reranked_count` vs `inputs.candidate_count` |

### 4.3 Streaming (`/api/query/stream`)

The streaming path uses the same trace tree. The only difference: `llm_chat_completion_stream` emits tokens incrementally but the final `end_span` records `answer_length` + `latency_ms` just like the non-streaming path.

---

## 5. Trace Tree: Ingestion Pipeline

### 5.1 Overview — two workers, one trace each

The ingestion pipeline is split across two worker processes. Each creates its **own root span**:

```
┌─────────────────────────────────────────────┐
│    cpu_worker: process_cpu_job (chain)      │
│    ┌─────────────────────────────────────┐  │
│    │ transcript extraction (not traced)  │  │
│    ├─────────────────────────────────────┤  │
│    │ llm_chat_completion_raw (llm)       │  │ ← classifier LLM
│    ├─────────────────────────────────────┤  │
│    │ embedding (embedding)               │  │ ← summary vector
│    └─────────────────────────────────────┘  │
│         │ queue → wiki_consumer             │
└─────────┼───────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────┐
│  wiki_consumer: process_wiki_job (chain)    │
│  ┌───────────────────────────────────────┐  │
│  │ llm_chat_completion_reasoning (llm)   │  │ ← Pass 1: chunk extraction
│  │ llm_chat_completion_reasoning (llm)   │  │ ← Pass 2: cause-effect analysis
│  │ llm_chat_completion_reasoning (llm)   │  │ ← Pass 3: compose wiki page
│  │ llm_chat_completion (llm)             │  │ ← event extraction
│  │ embedding (embedding) × N             │  │ ← knowledge retrieval
│  ├───────────────────────────────────────┤  │
│  │ section_embedding (embedding)         │  │ ← Ollama bge-m3 × N sections
│  │   └── (4 concurrent Ollama requests)  │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

### 5.2 Why two separate traces?

`cpu_worker` dequeues `source_items` from PostgreSQL (`SELECT ... FOR UPDATE SKIP LOCKED`), runs extract + classify, then pushes the job ID to a Redis queue. `wiki_consumer` independently BLPOPs from Redis and runs the 3-pass integrator + section embedding. They are separate processes with different scaling profiles.

Creating two root traces (`process_cpu_job` and `process_wiki_job`) is by design — they represent two distinct execution phases. To correlate them, both spans carry `video_id` in their inputs.

### 5.3 What to look for in LangSmith

| Signal | Where to check |
|--------|---------------|
| Classifier accuracy | `llm_chat_completion_raw` → `outputs.answer_preview` shows classifier JSON |
| Pass 1 fact count | First `llm_chat_completion_reasoning` → `outputs.answer_length` (larger = more facts) |
| Wiki quality | Compare `process_wiki_job` → `outputs.page_title`, `outputs.section_count` |
| Embedding performance | `section_embedding` → `outputs.section_count` and metadata `latency_ms` per section |
| Cached-page retries | `process_wiki_job` → metadata `fast_path: true` (skipped wiki, embedding only) |
| Failed jobs | Search LangSmith for `error` field — shows the exact failure point |

---

## 6. How to Observe in LangSmith Web UI

### 6.1 Access

1. Go to **https://smith.langchain.com**
2. Select project **`llm-wiki-rag`**
3. You'll see the **Runs** list — each row is a root trace or isolated span.

### 6.2 Navigating the trace tree

1. Click any trace named `rag_query`, `process_cpu_job`, or `process_wiki_job`.
2. The **Trace View** shows a tree on the left. Expand nodes to drill into children.
3. Click any node to see:
   - **Inputs** — what was passed to this step (messages, search queries, parameters).
   - **Outputs** — what was returned (answer preview, result count, embeddings).
   - **Metadata** — `latency_ms`, `tokens_used`, `prompt_tokens`, `completion_tokens`, `error_type`.
   - **Error** — if the span failed, the error message.
4. The **Timeline** tab shows a waterfall chart — useful for spotting bottlenecks.

### 6.3 Filtering & searching

| Goal | LangSmith action |
|------|-----------------|
| See all query traces | Filter: `run_type = chain`, search `rag_query` |
| Find slow queries | Sort by `Latency` descending, check `latency_ms` in metadata |
| Find errors | Filter: `Error = True` |
| Check cache hit rate | Search `cache_get`, scan outputs |
| See ingestion for a specific video | Search `video_id = <YouTube-ID>` |
| Check token costs | Filter `run_type = llm`, sum `tokens_used` in metadata |
| Verify 3-pass integrator | Open any `process_wiki_job`, check for 3 `llm_chat_completion_reasoning` children |

### 6.4 Dashboard views

LangSmith auto-generates charts on the project overview page:

- **Run count over time** — traffic/cost patterns
- **Latency distribution (P50/P95/P99)** — performance trends
- **Error rate** — stability signal
- **Token usage** — cost tracking

---

## 7. Design Decisions

### 7.1 `parent_run.create_child()` not `RunTree(parent=...)`

The LangSmith SDK documentation is ambiguous about this, but empirically: `RunTree(parent=parent_run)` creates a new root run that references the parent ID in metadata. It does NOT create a child within the parent's trace. The UI shows them as flat, isolated runs.

`parent_run.create_child()` links into the parent's `dotted_order`/trace ID, producing a proper hierarchical tree. This is now the canonical pattern in our adapter.

### 7.2 Traced wrappers instead of inline instrumentation

We could have added `start_span`/`end_span` calls directly inside `QueryPipeline.execute()`. That would work but:

- **Couples** business logic to telemetry concerns.
- **Requires** every use case to know about `TelemetryPort`.
- **Breaks** Single Responsibility.

The wrapper pattern keeps telemetry orthogonal. A use case calls `self._llm.chat_completion(...)` — whether the LLM is traced or raw is irrelevant to the use case code.

### 7.3 One adapter per process, one span per job

- **Factory scope:** `create_telemetry_adapter()` is called once at module import in `wiki_consumer.py` and `cpu_worker.py`. The adapter lives for the process lifetime.
- **Span scope:** Each `process_wiki_job` / `process_cpu_job` / `rag_query` creates a **new** root span via `start_span(parent=None)`. Spans are garbage-collected after `end_span`.
- **Parent wiring:** The call site passes `root_span` to traced wrappers via `set_parent_span()`. All nested spans automatically nest under the root.

### 7.4 Graceful degradation

All LangSmith calls (`start_span`, `end_span`, `add_metadata`, `create_child`, `post`, `patch`, `end`) are wrapped in `try/except` with `logger.debug` on failure. The observable result: traces are missing from LangSmith, but the RAG pipeline continues with zero impact.

### 7.5 Extract stage is intentionally not traced

`faster-whisper` transcription and `yt-dlp` download are CPU-bound operations with no structured inputs/outputs suitable for tracing. They would add noise. The extract result is captured indirectly: `process_cpu_job` → `outputs.transcript_segments` records segment count on success, and `error` records the failure reason when it fails.

---

## 8. Configuration

```bash
# In .env or K8s ConfigMap
LANGSMITH_TRACING=true                              # Enable/disable entirely
LANGSMITH_ENDPOINT=https://api.smith.langchain.com  # LangSmith API
LANGSMITH_API_KEY=lsv2_pt_...                       # Project API key
LANGSMITH_PROJECT=llm-wiki-rag                      # Project name in LangSmith
LANGSMITH_EVALUATOR_MODEL=deepseek-v4-flash          # Model name for metadata
```

When `LANGSMITH_TRACING=false` or `LANGSMITH_API_KEY` is empty, the Null adapter is used — zero telemetry overhead, zero network calls.

---

## 9. File Index

```
src/llm_wiki/
├── application/ports/telemetry/
│   └── telemetry_port.py                    # TelemetryPort + TelemetrySpan
├── infrastructure/telemetry/
│   ├── __init__.py                          # create_telemetry_adapter() factory
│   ├── langsmith_telemetry_adapter.py       # LangSmith implementation
│   └── null_telemetry_adapter.py            # No-op fallback
├── infrastructure/llm/
│   ├── traced_llm_wrapper.py                # TracedLLMWrapper (full content, no redaction)
│   └── traced_guardrail_analyzer_wrapper.py # TracedGuardrailAnalyzerWrapper
├── infrastructure/embedding/
│   └── traced_embedding_wrapper.py          # TracedEmbeddingWrapper
├── infrastructure/search/
│   ├── traced_search_wrapper.py             # TracedVectorSearchWrapper + TracedKeywordSearchWrapper
│   └── traced_event_search_wrapper.py       # TracedEventSearchWrapper (event_search + event_keyword_search)
├── infrastructure/persistence/redis/
│   └── traced_cache_wrapper.py              # TracedCacheWrapper
├── infrastructure/entrypoints/
│   ├── wiki_consumer.py                     # Root span: process_wiki_job
│   └── cpu_worker.py                        # Root span: process_cpu_job
└── presentation/
    └── dependencies.py                      # traced_llm(), traced_embedder() factories
```

---

## 10. Extending the Telemetry System

### Adding a new traceable operation

1. If it's a **wrapper around an existing port**, create a new `Traced*Wrapper` class following the pattern in `traced_llm_wrapper.py`.
2. If it's a **span inside business logic**, inject the telemetry adapter and call `start_span`/`end_span` directly — but consider: can this be a wrapper instead?
3. If it's a **new root span**, follow the pattern in `wiki_consumer.py`: create the span at the entry point, wire it to wrappers via `set_parent_span()`, and ensure all early-return paths call `end_span`.

### Adding a new observability backend

1. Implement `TelemetryPort` — e.g., `OpenTelemetryAdapter`.
2. Update `create_telemetry_adapter()` to select the adapter based on config.
3. The rest of the codebase (wrappers, use cases, workers) does not change — they only know about `TelemetryPort`.
