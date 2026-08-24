# System Architecture — LLM Wiki

> Architecture decisions, layer discipline, component catalog, and known constraints.
> For search strategy deep-dive, see [`search-strategy.md`](search-strategy.md).
> For observability (metrics, logs, traces, alerts), see [`observability-guide.md`](observability-guide.md) and [`telemetry-implementation-strategy.md`](telemetry-implementation-strategy.md).
> For code conventions, see [`conventions.md`](conventions.md).

---

## 1. Architecture Style

**Clean Architecture** (Ports & Adapters / Onion). Business logic (`domain` + `application`) is isolated from frameworks. You can swap databases or LLM providers without touching use cases.

### Layer Discipline

| Layer | Directory | Allowed Imports | Forbidden |
|-------|-----------|----------------|-----------|
| Domain | `src/llm_wiki/domain/` | stdlib, `dataclasses`, `uuid` | FastAPI, SQLAlchemy, Redis, httpx |
| Application | `src/llm_wiki/application/` | domain, ports (ABCs) | FastAPI, concrete adapters |
| Infrastructure | `src/llm_wiki/infrastructure/` | domain, application, any lib | — |
| Presentation | `src/llm_wiki/presentation/` | domain, application, FastAPI | infrastructure directly (use DI) |

---

## 2. Domain Layer (`domain/`)

Pure business rules. No framework imports.

### Entities
Plain `@dataclass` objects: `Page`, `Source`, `SourceItem`, `EventCanonical`, `Entity`, `MediaAsset`, `WorkerHeartbeat`, `ApiKey`, `CronJob`.

Source: [`src/llm_wiki/domain/entities/`](../src/llm_wiki/domain/entities/)

### Value Objects
Immutable wrappers: `SourceId`, `PageId`, `EventId`, `SourceItemId` (frozen, wrapping `UUID`); `Embedding` (validates 1024-dim vectors); `SearchResult` (uniform result shape for all search adapters); `Status` enums (`SourceItemStatus`, `PageStatus`, `EventStatus`, `EntityType`).

Source: [`src/llm_wiki/domain/value_objects/`](../src/llm_wiki/domain/value_objects/)

### Exceptions
`DomainException` base class. Business errors throw subclasses; the presentation layer maps them to HTTP codes.

Source: [`src/llm_wiki/domain/exceptions.py`](../src/llm_wiki/domain/exceptions.py)

---

## 3. Application Layer (`application/`)

Use cases, ports (ABCs), and DTOs. This layer never imports SQLAlchemy, Redis, or HTTP clients directly.

### 3.1 Use Cases

Each use case is a class with one primary `execute(...)` method.

| Use Case | Source | Responsibility |
|----------|--------|----------------|
| `AskQuestionUseCase` | `use_cases/query/ask_question.py` | Non-streaming RAG pipeline orchestration |
| `StreamAnswerUseCase` | `use_cases/query/stream_answer.py` | SSE streaming RAG via `QueryPipeline.execute_stream()` |
| `QueryPipeline` | `use_cases/query/pipeline.py` | **Core orchestrator:** cache → embed → guardrail+intent → 4-stream retrieve → RRF → rerank → diversity cap → synthesis → cache save |
| `SelfReflectiveRAGPipeline` | `use_cases/query/reflective_pipeline.py` | **Agentic RAG:** wraps `QueryPipeline`, evaluates quality, retries with alternate strategies |
| `IntegrateWikiUseCase` | `ingestion/wiki_integrator.py` | 3-pass LLM integrator: extract → analyze → write |
| `ProcessVideoUseCase` | `ingestion/process_video.py` | Full video-to-wiki pipeline with retry |
| `ExtractEventsUseCase` | `ingestion/extract_events.py` | LLM-powered event/entity extraction from pages |
| `SummarizeTimeRangeUseCase` | `query/summarize_time_range.py` | Time-range summary generation |

Source: [`src/llm_wiki/application/use_cases/`](../src/llm_wiki/application/use_cases/)

### 3.2 Ports (Abstract Interfaces)

Every external dependency has a port defined here before an adapter exists in infrastructure.

#### Repositories
| Port | Source |
|------|--------|
| `SourceRepository`, `SourceItemRepository` | `ports/repositories/source_repository.py` |
| `PageRepository`, `PageSectionRepository` | `ports/repositories/page_repository.py` |
| `EventRepository` | `ports/repositories/event_repository.py` |
| `EntityRepository` | `ports/repositories/entity_repository.py` |

#### Search & Analysis
| Port | Source | Role |
|------|--------|------|
| `VectorSearchPort` | `ports/search/vector_search.py` | Dense vector search (pgvector HNSW) |
| `KeywordSearchPort` | `ports/search/vector_search.py` | Sparse keyword search (tsvector) |
| `LLMClientPort` | `ports/search/vector_search.py` | LLM API abstraction |
| `EmbeddingServicePort` | `ports/search/vector_search.py` | Embedding generation |
| `CacheServicePort` | `ports/search/vector_search.py` | Answer caching |
| `GuardrailAnalyzerPort` | `ports/search/guardrail_analyzer_port.py` | **Active:** unified guardrail + intent + per-tool search inputs (single LLM call) |
| `EventSearchPort` | `ports/search/event_search_port.py` | Dense + sparse event search |
| `QueryExpanderPort` | `ports/search/query_expander_port.py` | Synonym generation for keyword expansion |
| `RerankerPort` | `ports/search/reranker_port.py` | LLM-based relevance scoring |
| `AnswerEvaluatorPort` | `ports/search/answer_evaluator_port.py` | Faithfulness/completeness/relevance scoring (0–10) |
| `GraphRAGPort` | `ports/search/graph_rag_port.py` | Entity→event graph traversal |

**Legacy ports** (kept for backward compat, not wired into pipeline): `QueryAnalyzerPort`, `QueryRewriterPort`.

#### Telemetry
| Port | Source | Role |
|------|--------|------|
| `TelemetryPort` | `ports/telemetry/telemetry_port.py` | LangSmith / null tracing |
| `MetricsPort` | `ports/telemetry/metrics_port.py` | Prometheus RED + business metrics |

Source: [`src/llm_wiki/application/ports/`](../src/llm_wiki/application/ports/)

### 3.3 DTOs

Plain dataclasses crossing the application boundary: `QueryInput`, `QueryResult`, `QueryResponse`, `SourceCreateRequest`, `SourceResponse`, admin DTOs.

Source: [`src/llm_wiki/application/dto/`](../src/llm_wiki/application/dto/)

---

## 4. Infrastructure Layer (`infrastructure/`)

Concrete adapters implementing ports.

### 4.1 Persistence

| Component | Source | Notes |
|-----------|--------|-------|
| ORM models | `persistence/postgres/models.py` | SQLAlchemy, mirrors domain entities with indexes, pgvector, tsvector, JSONB |
| Mappers | `persistence/postgres/mappers.py` | `to_domain(orm)` / `to_orm(domain)` — **only place** ORM becomes domain |
| Repositories | `persistence/postgres/repositories/` | Concrete implementations of repository ports |
| Database | `persistence/postgres/database.py` | `async_session_factory`, `get_db()` dependency |
| Redis cache | `persistence/redis/cache_adapter.py` | Implements `CacheServicePort`; failures logged and swallowed |

### 4.2 LLM & AI Services

| Component | Source | Implements |
|-----------|--------|------------|
| OpenAI adapter (multi-provider) | `llm/openai_adapter.py` | `LLMClientPort` |
| Guardrail+intent analyzer | `llm/guardrail_analyzer_adapter.py` | `GuardrailAnalyzerPort` **(active)** |
| Query expander | `llm/query_expander_adapter.py` | `QueryExpanderPort` |
| Reranker | `llm/reranker_adapter.py` | `RerankerPort` |
| Answer evaluator | `llm/answer_evaluator_adapter.py` | `AnswerEvaluatorPort` |
| API key manager | `llm/api_key_manager.py` | Multi-provider key rotation with rate-limit tracking |
| Legacy analyzers | `llm/query_analyzer_adapter.py`, `llm/query_rewriter_adapter.py` | Superseded, kept for backward compat |

### 4.3 Embedding

| Component | Source | Notes |
|-----------|--------|-------|
| Ollama adapter | `embedding/ollama_adapter.py` | `bge-m3` model, 1024-dim vectors |

### 4.4 Search

| Component | Source | Notes |
|-----------|--------|-------|
| pgvector (dense) | `search/pgvector_adapter.py` | HNSW cosine-distance, `TimeRange` filter, recency decay |
| tsvector (sparse) | `search/tsvector_adapter.py` | Persisted `fts_vector` on `page_sections`, VI diacritics preserved |
| Event search | `search/event_search_adapter.py` | Dense (pgvector) + sparse (tsvector) over `event_observations` |
| GraphRAG | `search/graph_rag_adapter.py` | Entity→event traversal via `event_entity_links` |
| Cross-encoder | `search/cross_encoder_reranker_adapter.py` | `BAAI/bge-reranker-v2-m3`, optional (`CROSS_ENCODER_ENABLED`) |

### 4.5 Telemetry & Observability

| Component | Source | Notes |
|-----------|--------|-------|
| LangSmith tracing | `telemetry/langsmith_telemetry_adapter.py` | Hierarchical trace trees via `parent_run.create_child()` |
| Null telemetry | `telemetry/null_telemetry_adapter.py` | No-op when `LANGSMITH_TRACING=false` |
| Prometheus metrics | `telemetry/prometheus_metrics_adapter.py` | Lazy metric creation, Port/Adapter pattern |
| Null metrics | `telemetry/null_metrics_adapter.py` | No-op when `ENABLE_METRICS=false` |
| Business metrics helpers | `telemetry/business_metrics.py` | `inc_counter`, `track_duration`, `set_gauge` |
| JSON logging | `telemetry/logging_config.py` | `trace_id`/`span_id` correlation, `LOG_FORMAT=text\|json` |

Traced wrappers decorate ports with LangSmith spans without changing use-case logic. See [`docs/telemetry-implementation-strategy.md`](telemetry-implementation-strategy.md) for the full trace tree architecture.

### 4.6 Entrypoints

| Component | Source | Purpose |
|-----------|--------|---------|
| CPU worker | `infrastructure/entrypoints/cpu_worker.py` | Background job execution |
| Wiki consumer | `infrastructure/entrypoints/wiki_consumer.py` | Ingestion pipeline consumer |
| Health server | `infrastructure/entrypoints/health_server.py` | Worker health API (port 8100) |

---

## 5. Presentation Layer (`presentation/`)

FastAPI routers, Pydantic schemas, middleware, and DI container.

### Routers

Source: [`src/llm_wiki/presentation/routes/`](../src/llm_wiki/presentation/routes/)

### Schemas

Pydantic request/response models — the public API contract. Must match `frontend/types/index.ts`.

Source: [`src/llm_wiki/presentation/schemas/common.py`](../src/llm_wiki/presentation/schemas/common.py)

### Middleware

| File | Purpose |
|------|---------|
| `middleware/error_handler.py` | Maps `DomainException` subclasses to HTTP codes |
| `middleware/request_logging.py` | Logs all requests |

### Route Registration

`main.py` registers routers in order. **The first matching route wins.** Known case: `GET /api/pages/{slug}` in `routes/pages.py` shadows the richer version in `routes/stubs.py` because `pages.router` is registered first.

`routes/stubs.py` is only mounted when `ENABLE_STUB_ROUTES=true`. Admin-only routes should live there only if not production-ready; prefer promoting mature routes to their own canonical router.

---

## 6. Dependency Injection

`presentation/dependencies.py` defines a `dependency-injector` `Container`.

**Pattern:** Stateless singletons (embedder, LLM client, cache) → DI container. Per-request objects (repositories, search adapters needing `AsyncSession`) → constructed in route handlers via `Depends(get_db)`.

The DI container declares some factories with `None` for session-bound dependencies; the real objects are built per-request in route handlers.

Source: [`src/llm_wiki/presentation/dependencies.py`](../src/llm_wiki/presentation/dependencies.py)

---

## 7. Database Schema (Key Tables)

| Table | Purpose |
|-------|---------|
| `sources` | YouTube channels / data sources |
| `source_items` | Individual videos/items to ingest |
| `pages` | Wiki pages; `summary_vector` for page-level search |
| `page_sections` | Page sections; `section_vector` (HNSW) + `fts_vector` (tsvector) |
| `page_links` | Manual/extracted cross-page links |
| `media_assets` | MinIO-stored files referenced by pages/sections |
| `event_canonicals` | Canonical events extracted across pages |
| `event_observations` | Per-page evidence for events |
| `event_timeline_chains` | Causal/temporal links between events |
| `entities` | People, organizations, locations, concepts, products |
| `event_entity_links` | Many-to-many: event ↔ entity |
| `entity_relations` | Typed relations between entities |
| `ingestion_logs` | Audit trail of ingestion events |
| `worker_heartbeats` | Worker liveness and current job |
| `api_keys` | Rotating LLM provider keys with priority & rate-limit |
| `cron_jobs` | Managed background tasks |
| `telegram_subscribers` | Telegram bot subscribers |

**Vector dimensions:** All `Vector(1024)` columns expect `bge-m3` embedding model.

Source: [`src/llm_wiki/infrastructure/persistence/postgres/models.py`](../src/llm_wiki/infrastructure/persistence/postgres/models.py)

---

## 8. Frontend Architecture

**Stack:** Next.js 14 App Router, React 18, TypeScript, TanStack Query v5, Tailwind CSS + shadcn/ui primitives, `react-force-graph-3d`, `@xyflow/react`, `react-markdown`.

**API client:** `frontend/lib/api-client.ts` is the single source of truth for backend calls. Base URL is `NEXT_PUBLIC_API_URL || "/api"`. In K8s, Next.js `rewrites()` proxies `/api/*` to `backend-v2.llm-wiki.svc.cluster.local:8000/api/*`.

**Streaming:** `frontend/app/api/query/stream/route.ts` proxies the SSE stream to avoid CORS and long-timeout issues.

**Type contract:** `frontend/types/index.ts` defines the shapes the frontend expects. The backend must match. `tests/test_all_apis.py` verifies this contract.

See [`frontend/AGENTS.md`](../frontend/AGENTS.md) for Docker build details and environment constraints.

---

## 9. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Clean Architecture** | Business logic isolated from frameworks; swap DBs or LLM providers without touching use cases |
| **Async-first** | FastAPI + SQLAlchemy async (`asyncpg`) + `httpx` |
| **Multi-stream hybrid search** | 4 retrieval streams (pgvector dense, tsvector keyword, event dense, event keyword) + GraphRAG merged via RRF with intent-aware weights |
| **Self-reflective RAG** | Pipeline evaluates its own answer and retries with alternate strategies (HyDE, decompose, expand) if below quality threshold |
| **Redis answer cache** | Three-tier: exact-match SHA256, semantic cosine ≥0.80, variable TTL (1h/24h). Cache failures degrade gracefully |
| **Unified guardrail + intent** | Single lightweight LLM call replaces old rewrite→analyze chain; extracts guardrail, intent, time_range, entities, per-tool search inputs, sub-questions, language |
| **Per-tool search inputs** | Separate `embedding_text`, `page_search_query`, `event_search_query` per retrieval stream |
| **LLM-based reranking** | LLM re-ranks top candidates; optional Cross-Encoder (`BAAI/bge-reranker-v2-m3`) |
| **Multi-provider LLM** | API key rotation (OpenCode Zen, Gemini) with priority and rate-limit tracking |
| **Temporal filtering** | Parsed `TimeRange` applied to vector and keyword search with recency decay |
| **Bilingual search** | VI + EN keywords, answer language matches question |
| **Telemetry** | LangSmith tracing (every pipeline step) + JSON structured logging with `trace_id`; Prometheus metrics optional (`ENABLE_METRICS`, disabled by default) |
| **Ports as ABCs** | Every external service behind a port; infrastructure provides adapters |
| **DI** | `dependency-injector` wires singletons and per-request factories |

---

## 10. Ingress & Services (K8s)

| Host / Port | Target | Purpose |
|-------------|--------|---------|
| `:30080` | NodePort → frontend | Toàn app (frontend proxy `/api` nội bộ) |
| `:30081` | NodePort → backend | Backend API trực tiếp |
| `backend-v2:8000` | FastAPI | Internal backend service |
| `cpu-worker:8100` | Health API (sidecar) | Worker health / cron-job control |

See [`k8s/AGENTS.md`](../k8s/AGENTS.md) for deployment order, RBAC, and troubleshooting.

---

## 11. Environment Variables

All settings in `src/llm_wiki/config.py` via `pydantic-settings`. Required vars in [`.env.example`](../.env.example). Key feature flags:

| Variable | Default | Effect |
|----------|---------|--------|
| `REASONING_ENABLED` | `true` | Global model reasoning toggle (not a wiki switch) |
| `WIKI_CHUNKING_ENABLED` | `false` | Wiki Pass 1 chunked map-reduce extraction (long videos) |
| `WIKI_WRITE_THINKING_ENABLED` | `false` | Wiki Pass 2 write with thinking ON (large JSON, use cautiously) |
| `WIKI_REFLECT_ENABLED` | `false` | Wiki Pass 3 Reflect & Verify (thinking ON, bounded corrections) |
| `CROSS_ENCODER_ENABLED` | `false` | Enables BAAI/bge-reranker-v2-m3 (CPU-slow) |
| `LANGSMITH_TRACING` | `false` | Sends traces to LangSmith |
| `ENABLE_METRICS` | `false` | Exposes Prometheus `/api/metrics` |
| `ENABLE_STUB_ROUTES` | `false` | Mounts admin routes from `stubs.py` |
| `LOG_FORMAT` | `text` | `json` for structured logging |

---

## 12. Known Gotchas

1. **Route shadowing:** `GET /api/pages/{slug}` served by `pages.py`, not the richer `stubs.py` version. Check `main.py` registration order.
2. **DI None singletons:** Some factories pass `None` for session-bound deps; real objects built per-request.
3. **Chat session mutations:** POST/PUT/DELETE on `/api/chat/sessions` are stubs (501).
4. **Frontend streaming proxy:** SSE proxied through `frontend/app/api/query/stream/route.ts` to avoid CORS issues.
5. **Cache failures silent:** Redis down → no caching, system still works.
6. **Embedding dimension:** 1024-dim hardcoded in `Embedding` value object. Change requires updating all `Vector(...)` columns.
7. **Docker DNS:** Build with `--network=host` — default bridge can't resolve `registry.npmjs.org`.
8. **K8s CronJob RBAC:** Backend ServiceAccount needs `k8s/backend/rbac.yaml` for real K8s state reporting.
9. **Reflective pipeline latency:** 2–4 additional LLM calls per query when `REASONING_ENABLED=true`.
10. **Cross-encoder CPU cost:** ~1–2s/query on CPU without GPU.
11. **Legacy ports:** `QueryAnalyzerPort`, `QueryRewriterPort` and their adapters/wrappers exist but aren't wired into the pipeline.
