# Code Conventions — LLM Wiki

> Engineering rules, naming, patterns, and workflows for AI and human contributors.
> For architecture decisions and layer discipline, see [`system-architecture.md`](system-architecture.md).

---

## 1. Code Style

- Python 3.12+ syntax; line length 100; target `py312`
- `async`/`await` for all I/O; `AsyncSession` for DB
- `dataclasses` for domain objects and DTOs
- Pydantic v2 for request/response schemas
- Import order and formatting via `ruff` — run `ruff check .` and `ruff format .` before committing
- Pre-commit hooks in [`.pre-commit-config.yaml`](../.pre-commit-config.yaml)

Config: [`pyproject.toml`](../pyproject.toml), [`pytest.ini`](../pytest.ini)

---

## 2. Adding a New Domain Concept

1. Add dataclass to `src/llm_wiki/domain/entities/<feature>.py`
2. Add value object / ID / enum if needed in `src/llm_wiki/domain/value_objects/`
3. Add domain exception in `src/llm_wiki/domain/exceptions.py` (only if it's a business error)
4. Add repository port in `src/llm_wiki/application/ports/repositories/<feature>_repository.py`
5. Add use case in `src/llm_wiki/application/use_cases/<feature>/`
6. Add ORM model in `src/llm_wiki/infrastructure/persistence/postgres/models.py`
7. Add mapper in `src/llm_wiki/infrastructure/persistence/postgres/mappers.py`
8. Add concrete repository in `src/llm_wiki/infrastructure/persistence/postgres/repositories/<feature>_repository.py`
9. Add route in `src/llm_wiki/presentation/routes/<feature>.py` and schema in `src/llm_wiki/presentation/schemas/common.py`
10. Add TypeScript type in `frontend/types/index.ts` and API client function in `frontend/lib/api-client.ts`
11. Add test in `tests/test_all_apis.py` (and unit tests if needed)

---

## 3. Adding a New External Service

1. Define a port (ABC) in `src/llm_wiki/application/ports/search/` or `ports/repositories/`
2. Implement adapter in `src/llm_wiki/infrastructure/<category>/`
3. Wire adapter in `src/llm_wiki/presentation/dependencies.py` (or construct per-request in routes if it needs `AsyncSession`)
4. Update [`.env.example`](../.env.example) and `src/llm_wiki/config.py` if new settings are required

---

## 4. API Response Shapes

- **Never** return a raw ORM model or domain entity from a route. Always build a Pydantic/JSON response matching `frontend/types/index.ts`.
- When modifying an endpoint, update `01_API_list.md`, `frontend/types/index.ts`, `frontend/lib/api-client.ts`, and contract tests in parallel.
- The integration test `tests/test_all_apis.py` validates every endpoint against the frontend-expected shapes.

---

## 5. Route Registration

- `src/llm_wiki/main.py` registers routers in order. **First matching route wins.**
- If you add a path that overlaps an existing one, verify which router is served.
- Admin-only routes should live in `routes/stubs.py` and be gated by `ENABLE_STUB_ROUTES=true` only if genuinely not ready for production. Prefer moving admin routes to their own canonical router as they mature.

---

## 6. Error Handling

- Business errors throw `DomainException` subclasses (see `src/llm_wiki/domain/exceptions.py`)
- `presentation/middleware/error_handler.py` maps them to HTTP codes (404, 409, 400, 502, 429, 422, 500)
- Unexpected errors caught by `unhandled_exception_handler` and logged
- Never swallow exceptions silently in infrastructure adapters unless degradation is intentional (e.g., Redis cache failures)

---

## 7. Environment Variables

- All settings in `src/llm_wiki/config.py` via `pydantic-settings`
- Add new env vars to both [`.env.example`](../.env.example) and `config.py`
- Use `validation_alias` for uppercase env var names; defaults should be safe for local dev

---

## 8. Dependency Injection

- **Stateless singletons** (embedder, LLM client, cache) → DI container in `presentation/dependencies.py`
- **Per-request objects** (repositories, search adapters needing `AsyncSession`) → constructed in route handlers using `Depends(get_db)`
- Do not pass `AsyncSession` or request-scoped objects into the DI container as singletons

---

## 9. Testing

### Contract Tests
`tests/test_all_apis.py` — validates every endpoint against frontend-expected shapes. Run:

```bash
API_BASE_URL=http://localhost:8000 pytest tests/test_all_apis.py -v
```

### Critical-Only
```bash
./scripts/test-apis.sh --critical-only
```

### Unit Tests
- `tests/unit/domain/` — domain entity behavior
- `tests/unit/application/` — use-case logic

### Lint & Type Check
- `ruff check .` / `ruff format .`
- `mypy` (non-strict) with pydantic plugin

---

## 10. Conventional Commits

Format: `type(scope): description`. Keep commits focused — one change per commit.  
Config: [`.commitlintrc.yml`](../.commitlintrc.yml)

---

## 11. Frontend Conventions

| Rule | Detail |
|------|--------|
| API client | `frontend/lib/api-client.ts` — single source of truth for all backend calls |
| Types | `frontend/types/index.ts` — must match backend schemas |
| State | TanStack Query v5 for server state; React state for UI-only |
| Components | Feature folders under `frontend/components/`; shared primitives in `components/ui/` |
| Streaming | SSE proxied through `frontend/app/api/query/stream/route.ts` |
| Docker | Build with `--network=host`; see [`frontend/AGENTS.md`](../frontend/AGENTS.md) |
