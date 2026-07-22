# LLM Wiki — Clean Architecture RAG Knowledge System

> **Project version:** backend `2.0.0`, frontend `3.0.0`  
> **Primary language:** Python 3.12+ (backend), TypeScript / Next.js 14 (frontend)  
> **Architecture:** Clean Architecture (a.k.a. Onion / Ports & Adapters) with FastAPI.  
> **Agent notes:** see root [`AGENTS.md`](AGENTS.md), [`frontend/AGENTS.md`](frontend/AGENTS.md), and [`k8s/AGENTS.md`](k8s/AGENTS.md) for environment-specific gotchas.

---

## 1. Project Overview

LLM Wiki is a knowledge-aggregation system that:

1. **Ingests** multi-source content (currently YouTube videos via transcript / manual upload).
2. **Converts** raw content into structured wiki pages (`pages` + `page_sections`).
3. **Extracts** entities, events, and relations into a knowledge graph.
4. **Answers** natural-language questions through a RAG pipeline that combines vector search (pgvector) and full-text search (PostgreSQL `tsvector`), with optional time-range filtering and recency scoring, then synthesizes an answer via an OpenAI-compatible LLM.
5. **Observes** pipeline execution via optional LangSmith tracing and evaluation adapters.
6. **Exposes** everything through a Next.js 14 admin dashboard: chat, wiki browser, source management, progress monitoring, knowledge graph, cron-job administration, and worker administration.

This README is written for **AI agents and future developers**. It explains the architecture, the core patterns, and the rules you must follow when extending the codebase.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Next.js 14 Frontend                         │
│  App Router  •  TanStack Query  •  Tailwind + shadcn/ui primitives  │
│  /api/* rewrites → backend-v2:8000 (or NEXT_PUBLIC_API_URL)        │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ HTTP / SSE
┌──────────────────────────────────▼──────────────────────────────────┐
│                     FastAPI Backend (llm_wiki)                        │
│  ─────────────────────────────────────────────────────────────────  │
│  Presentation  │  Application  │  Domain  │  Infrastructure          │
│  routes/        use_cases/      entities/   persistence/              │
│  schemas/       ports/          value_objects  llm/                   │
│  middleware/    dto/            exceptions.py   search/               │
│                                                 embedding/            │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
┌───────▼──────┐  ┌────────────────▼────────────┐  ┌────────▼──────┐
│  PostgreSQL  │  │  Redis (cache / queues)      │  │  Ollama       │
│  + pgvector  │  │  Valkey 8                    │  │  bge-m3 embed │
│  + tsvector  │  │                              │  │               │
└──────────────┘  └─────────────────────────────┘  └───────────────┘
        │
        │  object storage
┌───────▼──────┐
│    MinIO     │
└──────────────┘
```

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| **Clean Architecture** | Business logic (`domain` + `application`) is isolated from frameworks (FastAPI, SQLAlchemy, Redis, Ollama). You can swap databases or LLM providers without touching use cases. |
| **Async-first** | FastAPI + SQLAlchemy async (`asyncpg`) + `httpx` for all external calls. |
| **Hybrid search** | Vector search alone is brittle; combining `pgvector` cosine similarity with `tsvector` keyword search via Reciprocal Rank Fusion (RRF) improves recall. |
| **Redis answer cache** | Three-tier caching: (1) exact-match via SHA256 of normalized question, (2) semantic cache via embedding cosine similarity (≥0.80), (3) variable TTL: 1h for time-sensitive questions, 24h for factual. Both `/api/query` and `/api/query/stream` (GUI chat) benefit. Cache failures are swallowed (degraded performance, not failure). |
| **Temporal filtering** | Questions like "trong tháng vừa qua" or "past 2 weeks" are parsed into a `TimeRange` and applied to both vector and keyword search; recency decay boosts newer pages. |
| **Telemetry** | Optional LangSmith tracing spans every pipeline step (embedding, search, synthesis, cache). Disabled by default via `LANGSMITH_TRACING=false`. |
| **Ports as abstract classes** | Every external service is hidden behind a port in `application/ports/`. Infrastructure provides concrete adapters. |
| **Dependency injection** | `dependency-injector` wires singletons (embedder, LLM, cache, telemetry) and per-request factories (pipeline, use cases). |

---

## 3. Repository Structure

```
32_LLM_wiki_clean_arch/
├── src/llm_wiki/                      # Backend Python package
│   ├── domain/                          # Pure business logic (no frameworks)
│   │   ├── entities/                    # Dataclasses: Page, Source, Event, Entity, …
│   │   ├── value_objects/               # Identifiers, Embedding, SearchResult, Status enums
│   │   └── exceptions.py                # Domain exception hierarchy
│   ├── application/                     # Use cases + ports + DTOs
│   │   ├── use_cases/                   # Query pipeline, ingestion, event extraction
│   │   ├── ports/                       # Abstract repository & service interfaces
│   │   └── dto/                         # Input/output dataclasses
│   ├── infrastructure/                  # Concrete adapters (I/O, frameworks)
│   │   ├── persistence/postgres/        # SQLAlchemy ORM, mappers, repositories
│   │   ├── persistence/redis/           # Redis cache adapter
│   │   ├── llm/                         # OpenAI-compatible LLM adapter
│   │   ├── embedding/                   # Ollama embedding adapter
│   │   ├── search/                      # pgvector + tsvector search adapters
│   │   ├── telemetry/                   # LangSmith / null telemetry adapters
│   │   └── entrypoints/                 # cpu_worker, wiki_consumer, health_server
│   ├── presentation/                    # FastAPI layer
│   │   ├── routes/                      # API routers
│   │   ├── middleware/                  # Error handling, request logging
│   │   ├── schemas/                     # Pydantic request/response models
│   │   └── dependencies.py              # DI container
│   ├── config.py                        # Pydantic-settings configuration
│   └── main.py                          # FastAPI app assembly
│
├── frontend/                            # Next.js 14 App Router
│   ├── app/                             # Routes (page.tsx, layout.tsx, API routes)
│   ├── components/                      # UI by feature (chat, wiki, kg, admin, …)
│   ├── hooks/                           # TanStack Query wrappers
│   ├── lib/                             # API client, colors, query keys, utils
│   ├── types/index.ts                   # Shared TypeScript interfaces
│   └── next.config.js                   # Standalone output + /api rewrites
│
├── tests/                               # Pytest suite
│   ├── test_all_apis.py                 # Contract/integration tests for all endpoints
│   ├── conftest.py                      # Async DB session fixture
│   ├── integration/                     # (reserved)
│   └── unit/                            # Domain / use-case unit tests
│
├── k8s/                                 # Kubernetes manifests (Kind / K3s)
│   ├── README.md                        # Deployment guide
│   ├── backend/                         # FastAPI deployment
│   ├── frontend/                        # Next.js deployment
│   ├── postgres/                        # PostgreSQL + pgvector
│   ├── redis/                           # Valkey
│   ├── minio/                           # S3-compatible object storage
│   ├── ollama/                          # Embedding / LLM inference
│   └── …
│
├── scripts/                             # Dev / deploy helpers
│   ├── dev-local.sh                     # Run backend locally against K8s services
│   ├── test-apis.sh                     # Run API contract tests
│   ├── sync-and-test.sh                 # Sync changed files into K8s pods
│   └── deploy-k8s.sh                    # Build & deploy Docker images to K3s
│
├── pyproject.toml                       # Python dependencies & tool config
├── pytest.ini                          # Pytest markers & options
├── Dockerfile                          # Multi-stage Python backend image
└── .env.example                        # Required environment variables
```

---

## 4. Clean Architecture Layers (the “core”)

### 4.1 `domain/` — Enterprise business rules

- **Entities** are plain `@dataclass` objects (`Page`, `Source`, `SourceItem`, `EventCanonical`, `Entity`, `MediaAsset`, `WorkerHeartbeat`, `ApiKey`, `CronJob`, …).
- **Value objects** are immutable:
  - `SourceId`, `PageId`, `EventId`, `SourceItemId` — frozen dataclasses wrapping `UUID`.
  - `Embedding` — validates 1024-dim vectors.
  - `SearchResult` — uniform result shape for all search adapters.
  - `Status` enums — `SourceItemStatus`, `PageStatus`, `EventStatus`, `EntityType`.
- **Domain exceptions** (`DomainException` base) are thrown by business logic and mapped to HTTP status codes in the presentation layer.

**Rule for AI agents:** `domain/` MUST NOT import FastAPI, SQLAlchemy, Redis, `httpx`, or any infrastructure library. If you add a new business concept, put it here first as a dataclass + exception + value object if needed.

### 4.2 `application/` — Application business rules

#### 4.2.1 `application/use_cases/`

Each use case is a single class with one primary `execute(...)` method.

| Use case | File | Responsibility |
|----------|------|----------------|
| `AskQuestionUseCase` | `query/ask_question.py` | Orchestrates the RAG pipeline for non-streaming queries. |
| `StreamAnswerUseCase` | `query/stream_answer.py` | Wraps `QueryPipeline.execute_stream()` for SSE. |
| `QueryPipeline` | `query/pipeline.py` | **Core orchestrator:** cache → embed → vector search → keyword search → RRF merge → LLM synthesis → cache save. |
| `IntegrateWikiUseCase` | `ingestion/integrate_wiki.py` | Splits markdown into sections, embeds them, persists page + sections. |
| `ProcessVideoUseCase` | `ingestion/process_video.py` | Takes a `SourceItem` with transcript, runs wiki integration, retries on failure. |
| `ExtractEventsUseCase` | `ingestion/extract_events.py` | Uses LLM to extract events/entities from a page and stores them in the knowledge graph. |
| `SummarizeTimeRangeUseCase` | `query/summarize_time_range.py` | Generates a summary of events/pages within a date range. |

**Rule for AI agents:** A use case should only know about **ports** (abstract interfaces) and **domain objects**. It never imports SQLAlchemy or HTTP clients directly.

#### 4.2.2 `application/ports/`

Abstract base classes that define the contract between application and infrastructure.

```
application/ports/
├── repositories/
│   ├── source_repository.py      # SourceRepository, SourceItemRepository
│   ├── page_repository.py        # PageRepository, PageSectionRepository
│   ├── event_repository.py       # EventRepository
│   └── entity_repository.py      # EntityRepository
├── search/
│   └── vector_search.py          # VectorSearchPort, KeywordSearchPort,
│                                 # LLMClientPort, EmbeddingServicePort, CacheServicePort
└── telemetry/
    └── telemetry_port.py         # TelemetryPort for LangSmith / null tracing
```

**Rule for AI agents:** When you add a new external dependency (e.g., a new LLM provider, a new vector DB, a new cache), define a port in `application/ports/` first, then implement the adapter in `infrastructure/`. The use case code should not change.

#### 4.2.3 `application/dto/`

Plain dataclasses for crossing the application boundary:
- `QueryInput` / `QueryResult` / `QueryResponse`
- `SourceCreateRequest` / `SourceResponse` (some overlap with Pydantic schemas — see below)
- `Admin` DTOs

### 4.3 `infrastructure/` — Frameworks & drivers

#### 4.3.1 Persistence

- **`persistence/postgres/models.py`** — SQLAlchemy ORM models. Mirrors domain entities but adds indexes, foreign keys, `pgvector` columns, computed `tsvector` columns, and JSONB fields.
- **`persistence/postgres/mappers.py`** — Bidirectional `to_domain(orm)` / `to_orm(domain, existing)` mappers. **This is the only place ORM models become domain entities.**
- **`persistence/postgres/repositories/*.py`** — Concrete repository implementations (`PostgresSourceRepository`, `PostgresPageRepository`, etc.). They depend on `AsyncSession` and use mappers.
- **`persistence/postgres/database.py`** — `async_session_factory`, `get_db()` FastAPI dependency, and a sync engine fallback for Alembic/psycopg2 operations.
- **`persistence/redis/cache_adapter.py`** — `RedisCacheAdapter` implementing `CacheServicePort`. Failures are logged and ignored.

#### 4.3.2 External AI services

- **`llm/openai_adapter.py`** — `OpenAIAdapter` implements `LLMClientPort`. Talks to any OpenAI-compatible endpoint (default: OpenCode Zen API with `deepseek-v4-flash`). Supports streaming and reports token usage.
- **`embedding/ollama_adapter.py`** — `OllamaEmbeddingAdapter` implements `EmbeddingServicePort`. Uses model `bge-m3` and produces 1024-dim vectors.
- **`llm/api_key_manager.py`** — Rotates multiple provider keys with rate-limit tracking.

#### 4.3.3 Search

- **`search/pgvector_adapter.py`** — `PgVectorSearchAdapter` implements `VectorSearchPort`. Uses HNSW cosine-distance queries on `page_sections.section_vector` and `event_canonicals.canonical_embedding`. Applies optional `TimeRange` filters and recency scoring (`EXP(-λ * days)`).
- **`search/tsvector_adapter.py`** — `TsVectorSearchAdapter` implements `KeywordSearchPort`. Uses the persisted `fts_vector` on `page_sections`. Query cleaning preserves Vietnamese diacritics. Also supports `TimeRange` filters and recency scoring.

#### 4.3.4 Telemetry

- **`telemetry/langsmith_telemetry_adapter.py`** — Records spans and metadata to LangSmith when `LANGSMITH_TRACING=true`.
- **`telemetry/langsmith_eval_adapter.py`** — Evaluates RAG outputs against labeled datasets (optional batch workflow).
- **`telemetry/null_telemetry_adapter.py`** — No-op adapter used when tracing is disabled.
- **Traced wrappers** (`llm/traced_llm_wrapper.py`, `embedding/traced_embedding_wrapper.py`, `search/traced_search_wrapper.py`, `persistence/redis/traced_cache_wrapper.py`) — Wrap ports to emit spans without changing use-case logic.

### 4.4 `presentation/` — FastAPI layer

- **`routes/`** — FastAPI routers grouped by feature: `health`, `query`, `sources`, `pages`, `search`, `stubs`.
- **`schemas/common.py`** — Pydantic request/response models. These are the **public API contract**.
- **`middleware/error_handler.py`** — Maps `DomainException` subclasses to HTTP codes (404, 409, 400, 502, 429, 422, 500).
- **`middleware/request_logging.py`** — Logs all requests.
- **`dependencies.py`** — DI container using `dependency-injector`.

#### Critical presentation detail: two `pages` routes

There are two implementations of `GET /api/pages/{slug}`:

1. `presentation/routes/pages.py` — registered first. Returns a **basic** page shape with sections, media, links joined manually.
2. `presentation/routes/stubs.py` — has a richer version, but is **shadowed** because of registration order.

> `main.py` registers `pages.router` before `stubs.router`. The richer endpoint in `stubs.py` is effectively unreachable for `/api/pages/{slug}`.

`stubs.py` is only mounted when `ENABLE_STUB_ROUTES=true`. It provides admin endpoints (`/progress`, `/workers`, `/system-stats`, `/graph`, `/entity-graph`, `/admin/*`, `/restart/*`, `/chat/*`, `/attention-items`).

**Rule for AI agents:** If you add a route that conflicts with another path, explicitly check registration order in `main.py`. When in doubt, give admin routes distinct prefixes or merge them into the canonical router.

---

## 5. Dependency Injection (DI)

`presentation/dependencies.py` defines a `dependency-injector` `Container`:

```python
class Container(containers.DeclarativeContainer):
    embedder      = providers.Singleton(OllamaEmbeddingAdapter, ...)
    llm_client    = providers.Singleton(OpenAIAdapter, ...)
    cache         = providers.Singleton(RedisCacheAdapter)

    query_pipeline = providers.Factory(
        QueryPipeline,
        embedder=embedder,
        vector_search=None,      # overridden per-request in routes
        keyword_search=None,     # overridden per-request in routes
        llm=llm_client,
        cache=cache,
    )
```

**Why some factories pass `None`:** Search adapters need a live `AsyncSession`, which is only available inside a request. So `query.py` builds the pipeline manually:

```python
def get_query_pipeline(db: AsyncSession = Depends(get_db)):
    return QueryPipeline(
        embedder=container.embedder(),
        vector_search=PgVectorSearchAdapter(db),
        keyword_search=TsVectorSearchAdapter(db),
        llm=container.llm_client(),
        cache=container.cache(),
    )
```

**Rule for AI agents:** Stateless singletons (embedder, LLM, cache) go in the DI container. Per-request objects (repositories, search adapters tied to a DB session) are constructed inside route handlers or dependencies using `Depends(get_db)`.

---

## 6. Core Flows

### 6.1 RAG Query Pipeline (`POST /api/query` & `/api/query/stream`)

```
User Question
    │
    ▼
[1. Question Normalization] ──▶ lowercase, strip punctuation, collapse whitespace
    │
    ▼
[2. Exact Cache Check] ──hit──▶ return cached answer (0 LLM cost)
    │ miss
    ▼
[3. Embed Question] ──▶ Ollama bge-m3  ──▶ Embedding(1024)
    │
    ▼
[4. Semantic Cache Check] ──▶ cosine similarity ≥ 0.80 against stored embeddings
    │ hit                              │ miss
    ▼                                  ▼
[return cached answer]       [5. Time Range Extraction]
    (embed cost only)              │
                                   ▼
                        [6. Vector Search] ──▶ pgvector HNSW cosine + time filter + recency
                                   │
                        [7. Keyword Search] ──▶ PostgreSQL tsvector + time filter + recency
                                   │
                                   ▼
                        [8. Reciprocal Rank Fusion] ──▶ merge + re-rank (k=60)
                                   │
                                   ▼
                        [9. Build Context] ──▶ top 20 sections, truncated to 2000 chars, cited [1]..[N]
                                   │
                                   ▼
                        [10. LLM Synthesis] ──▶ OpenAI-compatible, temp=0.3, max_tokens=16384
                                   │
                                   ▼
                        [11. Cache Save] ──▶ exact (TTL 1h/24h) + semantic (embedding)
                                   │
                                   ▼
                        [12. Telemetry Span] ──▶ LangSmith with full input/output
                                   │
                                   ▼
Response: {answer, sources, tokens_used, cache_hit, pipeline_steps}
```

Streaming version (`/api/query/stream`) follows the same flow: exact cache → embed → semantic cache → retrieval → LLM synthesis → cache save. Cache hits return immediately as a `type: "complete"` SSE event.

SSE events emitted:
- `status: processing` → `retrieving` → `thinking` → `summarizing`
- `chunk` / `token` → answer tokens (non-streaming LLM currently returns full answer at once)
- `complete` → {answer, citations, sources_used, tokens_used}

**Time range support:** Both `POST /api/query` and `POST /api/query/stream` accept `from_date`/`to_date`. The frontend types expose these as ISO strings (`frontend/types/index.ts` and `frontend/hooks/use-query-stream.ts`).

### 6.2 Ingestion Pipeline

```
Source (YouTube channel)
    │
    ▼
SourceItem (video) ──▶ download / transcribe ──▶ transcript_text
    │
    ▼
ProcessVideoUseCase ──▶ IntegrateWikiUseCase
    │
    ▼
Page + PageSections (markdown split by headings)
    │
    ▼
ExtractEventsUseCase ──▶ EventCanonical + Entity + EntityRelation
```

- `IntegrateWikiUseCase` splits markdown on `\n(?=#{1,3} )` and embeds each section.
- `ExtractEventsUseCase` prompts an LLM to return structured JSON events and links them to entities.
- Status machine for `SourceItem`: `pending → processing → completed | failed | no_captions | skipped | rate_limited | requires_membership`.

---

## 7. Database Schema (key tables)

| Table | Purpose |
|-------|---------|
| `sources` | YouTube channels / data sources. |
| `source_items` | Individual videos/items to ingest. |
| `pages` | Wiki pages generated from items. `summary_vector` for future page-level search. |
| `page_sections` | Sections of a page. Contains `section_vector` (HNSW) and `fts_vector` (persisted tsvector). |
| `page_links` | Manual / extracted links between pages. |
| `media_assets` | Files stored in MinIO, referenced by page/section. |
| `event_canonicals` | Canonical events extracted across pages. |
| `event_observations` | Per-page evidence for an event. |
| `event_timeline_chains` | Causal / temporal links between events. |
| `entities` | People, organizations, locations, concepts, products, … |
| `event_entity_links` | Many-to-many: event ↔ entity. |
| `entity_relations` | Typed relations between entities (e.g., `competes_with`, `located_in`). |
| `ingestion_logs` | Audit trail of ingestion events. |
| `worker_heartbeats` | Worker liveness and current job. |
| `api_keys` | Rotating LLM provider keys with priority & rate-limit tracking. |
| `cron_jobs` | Managed background tasks. |
| `telegram_subscribers` | Telegram bot subscribers. |

**Vector dimensions:** All `Vector(1024)` columns expect the `bge-m3` embedding model.

---

## 8. Frontend Architecture

### Stack

- **Next.js 14** App Router, `output: 'standalone'`
- **React 18**, TypeScript 5.6
- **TanStack Query v5** for server state
- **Tailwind CSS v3** + custom `components/ui/*` primitives (shadcn/ui style)
- **Graph visualization:** `react-force-graph-3d`, `@xyflow/react`, `@dagrejs/dagre`
- **Markdown:** `react-markdown` + `rehype-highlight` + `rehype-sanitize` + `remark-gfm`

### Key directories

```
frontend/
├── app/                 # Routes
│   ├── page.tsx         # Dashboard
│   ├── chat/            # Chat with streaming
│   ├── wiki/            # Wiki list + detail
│   ├── sources/         # Source CRUD
│   ├── kg/              # Knowledge graph
│   ├── admin/           # Admin panels
│   └── api/query/stream/route.ts   # Server-side SSE proxy
├── components/          # UI components
│   ├── ui/              # Primitive components (card, button, badge, …)
│   ├── chat/            # Chat-specific components
│   ├── wiki/            # Wiki-specific components
│   ├── kg/              # Graph components
│   ├── sources/         # Source forms/cards
│   ├── admin/           # Admin panels
│   ├── dashboard/       # Dashboard widgets
│   └── layout/          # Sidebar, header
├── hooks/               # TanStack Query hooks
├── lib/                 # API client, query keys, helpers
└── types/index.ts       # API contracts in TypeScript
```

### API client

`lib/api-client.ts` is the single source of truth for backend calls. All hooks use it. The base URL is `process.env.NEXT_PUBLIC_API_URL || "/api"`. In production/K8s, the Next.js rewrite proxies `/api/*` to `backend-v2.llm-wiki.svc.cluster.local:8000/api/*`.

### Streaming

- `frontend/app/api/query/stream/route.ts` proxies the SSE stream from the backend to the browser (avoids CORS and long-timeout issues).
- `frontend/hooks/use-query-stream.ts` consumes the SSE and updates React state.

### Type contract

`frontend/types/index.ts` defines the exact shapes the frontend expects. **The backend must match these shapes.** The integration test `tests/test_all_apis.py` verifies this contract.

---

## 9. API Surface

All backend endpoints are under `/api`. See `01_API_list.md` for full details.

### Core (always available)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | `{status, version, db, pending_count, requires_membership_count, failed_count}`. |
| POST | `/api/query` | Non-streaming RAG. Returns `{answer, sources, tokens_used, cache_hit, pipeline_steps}`. |
| POST | `/api/query/stream` | SSE streaming RAG. |
| GET | `/api/summarize` | Time-range summary: `{summary, time_range, stats, top_events, top_pages}`. |
| POST | `/api/sources` | Create source. |
| GET | `/api/sources` | List active sources as `{sources, total}`. |
| GET | `/api/pages/{slug}` | Get page detail (basic version from `pages.py`). |
| GET | `/api/pages` | Paginated page list `{items, total, page, per_page}`. |
| GET | `/api/search` | Full-text search: `{results: [{id,title,slug,summary,source_name,published_at}], total}`. |

### Admin / stubs (require `ENABLE_STUB_ROUTES=true`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/progress` | Ingestion dashboard stats. |
| GET | `/api/system-stats` | CPU/RAM/disk via `psutil`. |
| GET | `/api/workers` | Worker heartbeat list. |
| GET | `/api/attention-items` | Failed / needs-attention items. |
| GET/POST | `/api/graph`, `/api/entity-graph`, `/api/cluster-graph`, `/api/cluster-expand` | Graph data. `entity-graph` and `cluster-expand` now return real `entities` nodes and `entity_relations` edges. |
| GET/POST/PATCH/DELETE | `/api/sources/{id}/*` | Source detail, scan, items, skip/retry/transcript. |
| POST | `/api/restart/{id}`, `/api/restart/source/{id}` | Reset items to pending. |
| GET/POST/PUT/DELETE | `/api/admin/api-keys` | API key management. |
| GET/POST | `/api/admin/cron-jobs` | Cron job management. Status is real: `scheduled`, `running`, `error`, `stopped`, `not_found`, `no_workers`. |
| DELETE | `/api/admin/clear-alerts` | Clear ingestion logs. |
| GET/POST/PUT/DELETE | `/api/chat/sessions` | Chat sessions (currently stub). |

---

## 10. Development & Deployment

### Local development against K8s services

```bash
# 1. Ensure K8s services are running (see k8s/README.md)
# 2. Port-forward in separate terminals:
kubectl port-forward -n llm-wiki svc/postgres 5432:5432
kubectl port-forward -n llm-wiki svc/redis   6379:6379
kubectl port-forward -n llm-wiki svc/ollama  11434:11434

# 3. Create .env from .env.example, using localhost endpoints.

# 4. Backend
pip install -e ".[dev]"
PYTHONPATH=src:. uvicorn llm_wiki.main:app --reload --host 0.0.0.0 --port 8000

# 5. Frontend (another terminal)
cd frontend
npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000/api" > .env.local
npm run dev
```

### Useful scripts

| Script | Purpose |
|--------|---------|
| `./scripts/dev-local.sh` | Install deps and start backend locally (manual frontend step). |
| `./scripts/test-apis.sh [URL]` | Run contract tests against running backend. |
| `./scripts/test-apis.sh --critical-only` | Run only frontend-breaking tests. |
| `./scripts/sync-and-test.sh` | Copy changed files into K8s pods and run tests. |
| `./scripts/deploy-k8s.sh` | Build images and deploy to K3s. |

### K8s deployment

See `k8s/README.md` for full instructions. Highlights:

- Uses `kind` with `extraMounts` + `hostPath` for live code reload without rebuilding images.
- `backend-v2`, `cpu-worker`, and `wiki-consumer` all use the same backend image (`32_llm_wiki_clean_arch-backend:latest`).
- `cpu-worker` deployment includes an `api` sidecar container on port `8100` so the backend can forward cron-job start/stop requests.
- `backend-v2` runs under `serviceAccountName: backend-v2` so it can read K8s `cronjobs`/`jobs` via RBAC (`k8s/backend/rbac.yaml`).
- Frontend image is built standalone (no `initContainer`/`hostPath` volumes); `next.config.js` rewrites `/api/*` to the backend service.
- Ingress: `llm-wiki.local` → `/api` → backend, `/` → frontend. NodePort `30080` also exposed.

---

## 11. Testing Strategy

### `tests/test_all_apis.py`

A comprehensive integration / contract test suite. It validates every endpoint against the **frontend-expected shapes** and explicitly catches known mismatches:

- `GET /api/sources` must be `{sources: [], total: N}` not a flat array.
- `GET /api/pages` must include `page` and `per_page`.
- `GET /api/search` results must include `slug`, `summary`, `source_name`, `published_at`.
- `POST /api/query` must return `answer`, `sources`, `tokens_used`, `cache_hit`, `pipeline_steps`.
- `POST /api/query/stream` must emit `type: "token"` and `type: "complete"`.
- `GET /api/health` must include `pending_count`, `requires_membership_count`, `failed_count`.
- `GET /api/pages/{slug}` must include sections, media, links, source info.

Run it:

```bash
API_BASE_URL=http://localhost:8000 pytest tests/test_all_apis.py -v
```

### Unit tests

- `tests/unit/domain/test_entities.py` — domain entity behavior.
- `tests/unit/application/test_query_pipeline.py` — use-case logic.

### Lint / type check

- `ruff` (lint + format) configured in `pyproject.toml`.
- `mypy` (non-strict) with pydantic plugin.
- Pre-commit hooks in `.pre-commit-config.yaml`.

---

## 12. Conventions & Rules for AI Contributors

### 12.1 Code style

- Python 3.12+ syntax; line length 100; target `py312`.
- Use `async`/`await` for I/O. Use `AsyncSession` for DB work.
- Prefer `dataclasses` for domain objects and DTOs.
- Use Pydantic v2 for request/response schemas.
- Import order: `ruff` handles it; run `ruff check .` and `ruff format .` before committing.

### 12.2 Adding a new domain concept

1. Add dataclass to `domain/entities/<feature>.py`.
2. Add value object / ID / enum if needed in `domain/value_objects/`.
3. Add domain exception in `domain/exceptions.py` if it represents a business error.
4. Add repository port in `application/ports/repositories/<feature>_repository.py`.
5. Add use case in `application/use_cases/<feature>/`.
6. Add ORM model in `infrastructure/persistence/postgres/models.py`.
7. Add mapper in `infrastructure/persistence/postgres/mappers.py`.
8. Add concrete repository in `infrastructure/persistence/postgres/repositories/<feature>_repository.py`.
9. Add route in `presentation/routes/<feature>.py` and schema in `presentation/schemas/common.py`.
10. Add TypeScript type in `frontend/types/index.ts` and API client function in `frontend/lib/api-client.ts`.
11. Add test in `tests/test_all_apis.py` (and unit tests if needed).

### 12.3 Adding a new external service

1. Define a port in `application/ports/search/` or `application/ports/repositories/`.
2. Implement adapter in `infrastructure/<category>/`.
3. Wire adapter in `presentation/dependencies.py` (or construct per-request in routes if it needs `AsyncSession`).
4. Update `.env.example` and `config.py` if new settings are required.

### 12.4 API response shapes

- **Never** return a raw ORM model or domain entity directly from a route. Always build a Pydantic/JSON response that matches `frontend/types/index.ts`.
- When modifying an endpoint, update `01_API_list.md`, `frontend/types/index.ts`, `frontend/lib/api-client.ts`, and the contract tests.

### 12.5 Route registration

- `main.py` registers routers in order. The first matching route wins.
- If you add a path that overlaps an existing one, verify which router is served.
- Admin-only routes should live in `stubs.py` and be gated by `ENABLE_STUB_ROUTES=true` only if they are genuinely not ready for production. Prefer moving admin routes to their own canonical router as they mature.

### 12.6 Error handling

- Business errors throw `DomainException` subclasses.
- `presentation/middleware/error_handler.py` maps them to HTTP codes.
- Unexpected errors are caught by `unhandled_exception_handler` and logged.
- Never swallow exceptions silently in infrastructure adapters unless degradation is intentional (e.g., Redis cache failures).

### 12.7 Environment variables

- All settings live in `llm_wiki.config.settings` using `pydantic-settings`.
- Add new env vars to both `.env.example` and `config.py`.
- Use `validation_alias` for uppercase env var names; default values should be safe for local dev.

---

## 13. Known Issues & Gotchas

1. **Route shadowing:** `GET /api/pages/{slug}` is served by `pages.py`, not the richer version in `stubs.py`. If you need the enriched shape, either merge the implementations or change registration order.
2. **DI container passes `None` for session-bound deps:** `query_pipeline`, `integrate_wiki_use_case`, etc. are declared with `None` for repository/search args because they need a per-request `AsyncSession`. The real objects are built in route handlers or dependencies.
3. **Chat sessions use file-backed storage:** persisted to `CHAT_HISTORY_DIR` (default `/data/chat-history`). In K8s this is a `hostPath` volume. Sessions auto-title from the first user message.
4. **API key create/update endpoints return 501:** only list, delete, and activate are implemented.
5. **Frontend streaming proxy:** `frontend/app/api/query/stream/route.ts` proxies the SSE stream from the backend to the browser. In K8s it uses the internal service DNS; for local dev, set `NEXT_PUBLIC_API_URL`.
6. **Cache failures are silent:** if Redis is down, the system still works but answers are not cached.
7. **Embedding dimension mismatch:** `Embedding` validates 1024 dims. If you change the embedding model, update `Embedding.dimensions` and all `Vector(...)` columns in `models.py`.
8. **Docker DNS on this host:** the default Docker bridge cannot resolve `registry.npmjs.org` reliably. Build the frontend image with `--network=host` (see `frontend/AGENTS.md`).
9. **K8s CronJob status requires RBAC:** the backend ServiceAccount must be bound to `k8s/backend/rbac.yaml` before `/api/admin/cron-jobs` can report real K8s state.

---

## 14. Quick Reference

| I want to… | Look at / Run |
|------------|---------------|
| Understand the RAG pipeline | `src/llm_wiki/application/use_cases/query/pipeline.py` |
| Add a new API endpoint | `src/llm_wiki/presentation/routes/`, `schemas/common.py`, `tests/test_all_apis.py` |
| Add a new DB table | `models.py` → `mappers.py` → `repositories/` → `ports/` → `routes/` |
| Change LLM provider | `infrastructure/llm/openai_adapter.py` + `config.py` + `dependencies.py` |
| Change embedding model | `infrastructure/embedding/ollama_adapter.py` + `domain/value_objects/embedding.py` + `models.py` |
| Run all contract tests | `API_BASE_URL=http://localhost:8000 pytest tests/test_all_apis.py -v` |
| Deploy to K8s | `./scripts/deploy-k8s.sh` (K3s) or follow `k8s/README.md` (Kind) |
| Run frontend locally | `cd frontend && npm install && npm run dev` |
| Build frontend Docker image | `docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest -f frontend/Dockerfile frontend/` (see `frontend/AGENTS.md`) |
| Deploy to Kind | `k8s/AGENTS.md` for order + RBAC |

---

*Last updated for backend v2.1.1 / frontend v3.0.1 — added nodejs to Dockerfile runtime deps, fixed KG 3D viewport with ResizeObserver and proper containment, fixed started_at incorrectly cleared on job completion.*
