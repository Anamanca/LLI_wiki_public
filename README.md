[![CI](https://github.com/Anamanca/LLI_wiki_public/actions/workflows/ci.yml/badge.svg)](https://github.com/Anamanca/LLI_wiki_public/actions/workflows/ci.yml)

# LLM Wiki — Clean Architecture RAG Knowledge System

> **Version:** backend `2.2.0`, frontend `3.0.1`
> **Stack:** Python 3.12+ / FastAPI (backend), TypeScript / Next.js 14 (frontend)
> **Architecture:** Clean Architecture (Ports & Adapters)
> **Agent notes:** [`AGENTS.md`](AGENTS.md), [`frontend/AGENTS.md`](frontend/AGENTS.md), [`k8s/AGENTS.md`](k8s/AGENTS.md)

---

## 1. What It Does

LLM Wiki is a knowledge-aggregation system that:

1. **Ingests** multi-source content (YouTube transcripts, manual upload).
2. **Converts** raw content into structured wiki pages via a 3-pass LLM integrator: Pass 1 extracts structured facts (optionally chunked map-reduce for long videos), Pass 2 writes the page, Pass 3 reflects & verifies (numbers/dates/coverage). Feature flags default OFF.
3. **Extracts** entities, events, and relations into a knowledge graph.
4. **Answers** natural-language questions through an **agentic RAG pipeline**: hybrid search (pgvector HNSW + tsvector + event search + GraphRAG), RRF fusion, LLM reranking, self-reflective evaluation with retry, and three-tier caching.
5. **Observes** execution via LangSmith tracing + JSON structured logging with `trace_id` (Port/Adapter pattern; Prometheus metrics optional, disabled by default).
6. **Monitors** itself via worker heartbeats in Postgres, `scripts/healthcheck.sh`, ingestion alerts (Web UI + Telegram).
7. **Exposes** a Next.js admin dashboard: chat, wiki browser, source management, knowledge graph, cron-job and worker administration.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Next.js 14 Frontend                         │
│  App Router  •  TanStack Query  •  Tailwind + shadcn/ui             │
│  /api/* rewrites → backend-v2:8000 (or NEXT_PUBLIC_API_URL)        │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ HTTP / SSE
┌──────────────────────────────────▼──────────────────────────────────┐
│                     FastAPI Backend (llm_wiki)                       │
│  Presentation  │  Application  │  Domain  │  Infrastructure          │
│  routes/        use_cases/      entities/   persistence/              │
│  schemas/       ports/          value_objects  llm/                  │
│  middleware/    dto/            exceptions.py   search/               │
│  metrics/                                       embedding/            │
│                                                 telemetry/            │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
┌───────▼──────┐  ┌────────────────▼────────────┐  ┌────────▼──────┐
│  PostgreSQL  │  │  Redis (cache / queues)      │  │  Ollama       │
│  + pgvector  │  │  Valkey 8                    │  │  bge-m3 embed │
│  + tsvector  │  │                              │  │               │
└──────┬───────┘  └─────────────────────────────┘  └───────────────┘
       │
       │  object storage
┌──────▼──────┐
│    MinIO    │
└──────┬──────┘
       │
┌──────▼──────────────────────────────────────────────────────────────┐
│  Observability: LangSmith tracing + JSON logs (trace_id)             │
│  healthcheck.sh + worker heartbeats + Web/Telegram alerts            │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Clean Architecture** | Business logic isolated from frameworks; swap DBs or LLM providers without touching use cases. |
| **Async-first** | FastAPI + SQLAlchemy async (`asyncpg`) + `httpx` for all external calls. |
| **Multi-stream hybrid search** | 4 retrieval streams + GraphRAG merged via RRF with intent-aware weights and diversity capping. |
| **Self-reflective (Agentic) RAG** | Pipeline evaluates its own answer and retries with alternate strategies (HyDE, decompose, expand) if quality is below threshold. |
| **Redis answer cache** | Three-tier: exact-match (SHA256), semantic (cosine ≥0.80), variable TTL (1h/24h). Cache failures degrade gracefully. |
| **Unified guardrail + intent analysis** | Single LLM call replaces old rewrite→analyze chain. Extracts guardrail, intent (6 types), time_range, entities, per-tool search inputs, sub-questions, language. |
| **Per-tool search inputs** | Separate structured inputs per retrieval stream: `embedding_text`, `page_search_query`, `event_search_query`. |
| **LLM-based reranking** | LLM re-ranks top candidates; optional Cross-Encoder (`BAAI/bge-reranker-v2-m3`). |
| **Multi-provider LLM** | API key rotation across providers (OpenCode Zen, Gemini) with priority and rate-limit tracking. |
| **Temporal filtering** | Parsed `TimeRange` applied to search with recency decay. |
| **Bilingual search + synthesis** | VI + EN keywords; answer language matches question (LLM-detected with regex fallback). |
| **Telemetry** | LangSmith tracing (full LLM I/O) + JSON structured logging with `trace_id`; Prometheus metrics optional (`ENABLE_METRICS`, disabled by default). |
| **Ports as ABCs** | Every external service behind a port; infrastructure provides adapters. |
| **DI** | `dependency-injector` wires singletons (embedder, LLM, cache) and per-request factories (pipeline, repos). |

---

## 3. Repository Structure

```
32_LLM_wiki_clean_arch/
├── src/llm_wiki/                      # Backend Python package
│   ├── domain/                          # Pure business logic (no frameworks)
│   ├── application/                     # Use cases + ports + DTOs
│   ├── infrastructure/                  # Concrete adapters (I/O, frameworks)
│   ├── presentation/                    # FastAPI layer (routes, schemas, middleware, DI)
│   ├── config.py                        # Pydantic-settings configuration
│   └── main.py                          # FastAPI app assembly
├── frontend/                            # Next.js 14 App Router
│   ├── app/, components/, hooks/, lib/, types/index.ts
│   └── next.config.js                   # Standalone output + /api rewrites
├── tests/                               # Pytest suite (contract, integration, unit)
├── docs/                                # Technical documentation (see §4 below)
├── k8s/                                 # Kubernetes manifests (Kind / K3s)
├── scripts/                             # Dev / deploy helpers
├── pyproject.toml                       # Python dependencies & tool config
├── pytest.ini, Dockerfile, .env.example
└── k8s/migrations/                      # SQL migrations (manual apply, e.g. pass1_facts)
```

---

## 4. Documentation Map

| Document | Contents |
|----------|----------|
| [`docs/system-architecture.md`](docs/system-architecture.md) | Layer discipline, use-case catalog, ports inventory, adapters, DI pattern, DB schema, frontend architecture, K8s services, feature flags, known gotchas |
| [`docs/conventions.md`](docs/conventions.md) | Code style, adding concepts/services/APIs, route registration, error handling, env vars, testing, commits |
| [`docs/search-strategy.md`](docs/search-strategy.md) | Full RAG pipeline: cache, guardrail+intent analysis, 4-stream retrieval, RRF, reranking, synthesis, self-reflective loop |
| [`docs/telemetry-implementation-strategy.md`](docs/telemetry-implementation-strategy.md) | LangSmith tracing architecture, trace trees, span hierarchy |
| [`docs/observability-guide.md`](docs/observability-guide.md) | Observability: LangSmith tracing, healthcheck, worker heartbeats, alerts (monitoring stack đã gỡ) |
| [`docs/operations/wiki-extraction-v2-rollout.md`](docs/operations/wiki-extraction-v2-rollout.md) | Wiki Extraction v2 rollout runbook: flags, canary, thresholds, reprocess |
| [`01_API_list.md`](01_API_list.md) | Complete API reference (all endpoints, request/response shapes) |
| [`AGENTS.md`](AGENTS.md) | Agent context: Docker builds, cluster context, design constraints |
| [`frontend/AGENTS.md`](frontend/AGENTS.md) | Frontend Docker build, Next.js config, environment |
| [`k8s/AGENTS.md`](k8s/AGENTS.md) | K8s deployment order, RBAC, troubleshooting |
| [`scripts/AGENTS.md`](scripts/AGENTS.md) | Script usage guide for AI agents |

---

## 5. Core API Surface

All endpoints under `/api`. See [`01_API_list.md`](01_API_list.md) for full details.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | `{status, version, db, pending_count, ...}` |
| GET | `/api/metrics` | Prometheus text format (when `ENABLE_METRICS=true`) |
| POST | `/api/query` | Non-streaming RAG: `{answer, sources, tokens_used, ...}` |
| POST | `/api/query/stream` | SSE streaming RAG |
| GET | `/api/summarize` | Time-range summary: `{summary, time_range, stats, top_events, top_pages}` |
| POST | `/api/sources` | Create source |
| GET | `/api/sources` | List active sources `{sources, total}` |
| GET | `/api/pages/{slug}` | Page detail with sections, media, links |
| GET | `/api/pages` | Paginated list `{items, total, page, per_page}` |
| GET | `/api/search` | Full-text search `{results, total}` |
| GET/POST | `/api/admin/*` | Admin endpoints (require `ENABLE_STUB_ROUTES=true`) |

---

## 6. Development

### Truy cập cluster (không cần port-forward)

Frontend/backend là NodePort services, kind map host port 30080/30081 → node:

```bash
# Frontend (toàn app, proxy /api nội bộ)
curl http://localhost:30080/               # hoặc http://100.115.181.93:30080 (Tailscale)
# Backend API trực tiếp
curl http://localhost:30081/api/health
# Healthcheck toàn hệ thống
./scripts/healthcheck.sh
```

### Local dev chống lại services trong cluster

```bash
# 1. Truy cập Postgres/Redis/Ollama từ máy host (chỉ khi dev local, không phải lúc chạy cluster):
kubectl -n llm-wiki port-forward svc/postgres 5432:5432 &
kubectl -n llm-wiki port-forward svc/redis   6379:6379 &
kubectl -n llm-wiki port-forward svc/ollama  11434:11434 &

# 2. Create .env from .env.example

# 3. Backend
pip install -e ".[dev]"
PYTHONPATH=src:. uvicorn llm_wiki.main:app --reload --host 0.0.0.0 --port 8000

# 4. Frontend
cd frontend && npm install
echo "NEXT_PUBLIC_API_URL=http://localhost:8000/api" > .env.local
npm run dev
```

### Key scripts

| Script | Purpose |
|--------|---------|
| `./scripts/dev-local.sh` | Install deps + start backend locally |
| `./scripts/test-apis.sh [URL]` | Run contract tests against running backend |
| `./scripts/sync-and-test.sh` | Sync changed files into K8s pods + test |
| `./scripts/deploy-k8s.sh` | Build images + deploy to K3s |
| `./scripts/healthcheck.sh` | Healthcheck cluster + app + host resources |
| `python scripts/reprocess-wiki.py` | Force-reprocess completed wiki items (dry-run, flags `--external-id`, `--generation`) |
| `python scripts/benchmark_rag.py` | Benchmark RAG pipeline |
| `python scripts/eval_rag.py` | Evaluate RAG against labeled dataset (LangSmith) |

### Docker builds

```bash
# Backend
docker build --network=host -t 32_llm_wiki_clean_arch-backend:latest .

# Frontend
docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest -f frontend/Dockerfile frontend/
```

Both require `--network=host` — the default Docker bridge on this host cannot resolve external registries.

### K8s deployment

See [`k8s/README.md`](k8s/README.md) and [`k8s/AGENTS.md`](k8s/AGENTS.md). Key facts:

- `backend-v2`, `cpu-worker`, `wiki-consumer` share the same backend image.
- `cpu-worker` includes an `api` sidecar on port `8100` for cron-job control.
- Frontend image is standalone; Next.js `rewrites()` proxies `/api/*` to `backend-v2:8000`.
- Truy cập: frontend NodePort `:30080` (toàn app), backend NodePort `:30081` (API trực tiếp). Không cần port-forward.

---

## 7. Testing

```bash
# Contract tests (validate API shapes against frontend types)
API_BASE_URL=http://localhost:8000 pytest tests/test_all_apis.py -v

# Unit tests
pytest tests/unit/ -v

# Lint + type check
ruff check . && ruff format . --check
mypy src/
```

See [`docs/conventions.md`](docs/conventions.md) for the full testing strategy and quality gates.

---

*Last updated: backend v2.2.0 / frontend v3.0.1*
