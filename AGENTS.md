# Agent Guidelines — LLM Wiki

## Stack
- Backend: Python 3.11+, FastAPI, SQLAlchemy 2 async (`asyncpg`), Pydantic v2, `dependency-injector`.
- Frontend: Next.js 14 App Router, React 18, TypeScript, TanStack Query, Tailwind.
- Data: PostgreSQL + pgvector, Redis/Valkey, Ollama (`bge-m3`), MinIO.

## Layer Rules
1. `domain/` — pure dataclasses & exceptions. **No framework imports** (FastAPI, SQLAlchemy, httpx, Redis).
2. `application/` — use cases + ports (ABC). Only depends on `domain` and abstract ports.
3. `infrastructure/` — concrete adapters (Postgres, Redis, Ollama, OpenAI-compatible LLM). Uses ORM/mappers here only.
4. `presentation/` — FastAPI routes, Pydantic schemas, middleware, DI container.

## Adding Features

### New domain concept
1. `domain/entities/<x>.py` → dataclass.
2. `domain/value_objects/` → ID/enum if needed.
3. `domain/exceptions.py` → exception if business error.
4. `application/ports/repositories/<x>_repository.py` → ABC.
5. `application/use_cases/<feature>/<x>.py` → use case.
6. `infrastructure/persistence/postgres/models.py` → ORM.
7. `infrastructure/persistence/postgres/mappers.py` → `to_domain` / `to_orm`.
8. `infrastructure/persistence/postgres/repositories/<x>_repository.py` → impl.
9. `presentation/routes/<feature>.py` + `schemas/common.py` → API.
10. `frontend/types/index.ts` + `lib/api-client.ts` → frontend contract.
11. `tests/test_all_apis.py` → contract test.

### New external service
1. Define port in `application/ports/`.
2. Implement adapter in `infrastructure/<category>/`.
3. Wire in `presentation/dependencies.py` (singleton) or build per-request in routes if needs `AsyncSession`.
4. Add env var to `.env.example` and `config.py`.

## API Contract
- Response shapes must match `frontend/types/index.ts`.
- Never return raw ORM/domain objects from routes.
- Changing an endpoint → update: schema, frontend type, api-client, contract test, `01_API_list.md`.

## Commands
```bash
# Lint & format
ruff check . && ruff format .

# Type check
mypy src/llm_wiki

# Run contract tests
API_BASE_URL=http://localhost:8000 pytest tests/test_all_apis.py -v

# Local backend
PYTHONPATH=src:. uvicorn llm_wiki.main:app --reload --host 0.0.0.0 --port 8000

# Local frontend
cd frontend && npm install && npm run dev
```

## Critical Gotchas
- `GET /api/pages/{slug}` is served by `routes/pages.py` (registered before `stubs.py`). Richer stub version is shadowed.
- `ENABLE_STUB_ROUTES=true` required for admin endpoints (`/progress`, `/workers`, `/graph`, `/admin/*`, etc.).
- DI container declares some factories with `None` for session-bound deps; real objects are built inside route handlers using `Depends(get_db)`.
- Cache/Redis failures are silent (graceful degradation).
- Embedding dimension is fixed at 1024 (`bge-m3`). Change model → update `Embedding.dimensions` and all `Vector(1024)` columns.
- `SourceItem` statuses: `pending → processing → completed | failed | no_captions | skipped | rate_limited | requires_membership`.
