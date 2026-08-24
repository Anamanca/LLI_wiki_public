# Agent Notes — LLM Wiki Clean Architecture

This project is a FastAPI backend + Next.js frontend deployed on a local `kind` Kubernetes cluster (`kind-llm-wiki`, namespace `llm-wiki`).

## Domain-Specific Notes

- **Frontend build & Docker**: see [`frontend/AGENTS.md`](frontend/AGENTS.md)
- **Kubernetes deployment & backend RBAC**: see [`k8s/AGENTS.md`](k8s/AGENTS.md)

## High-Level Design Constraints

- Backend is the source of truth for ingestion, cron jobs, and worker status.
- Frontend renders read-only dashboards and admin panels; mutations go through the backend API.
- The frontend uses Next.js `rewrites()` to proxy `/api/*` to `backend-v2.llm-wiki.svc.cluster.local:8000` inside the cluster.
- Always use the repo's existing patterns (`src/llm_wiki/...`, `frontend/...`, `k8s/...`) before inventing new ones.

## Docker Build

Both backend and frontend images MUST be built with `--network=host`. The default Docker bridge on this machine cannot resolve external registries reliably (`EAI_AGAIN`). Without it, `pip install` or `npm ci` will hang or fail with DNS errors.

```bash
# Backend (from repo root)
docker build --network=host -t 32_llm_wiki_clean_arch-backend:latest .

# Frontend (from repo root)
docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest -f frontend/Dockerfile frontend/
```

### Backend Dockerfile
- Single `pyproject.toml` is the source of truth for Python dependencies — `pip install .` reads from it, no duplicate dependency list.
- Multi-stage: `builder` stage compiles deps, `runner` stage only copies installed packages + `src/`.
- `CMD` starts uvicorn on port 8000.

### Frontend Dockerfile
See [`frontend/AGENTS.md`](frontend/AGENTS.md) for details. Key: uses `npm ci` (needs `--network=host`), Next.js `output: 'standalone'`, runs as non-root `nextjs` user.

## Verified State (2026-07-21)

- Frontend image: `32_llm_wiki_clean_arch-frontend:latest`
- Backend image: `32_llm_wiki_clean_arch-backend:latest`
- Cluster context: `kind-llm-wiki`
- Namespace: `llm-wiki`
-
## Wiki Extraction v2 (2026-08-23)

- Feature flags (default OFF): `WIKI_CHUNKING_ENABLED`, `WIKI_WRITE_THINKING_ENABLED`,
  `WIKI_REFLECT_ENABLED` (config.py). `REASONING_ENABLED` is global — do NOT use it as the
  wiki-only switch.
- Chunked map-reduce extraction: `transcript_chunker.py` (600s chunks, 45s overlap, max 12)
  + `fact_merger.py` (field-preserving dedup; `relationships` vs `entity_relations` merge
  separately). Raw segments feed `start_time`/`source_quote` on numbers/events/key_claims.
- Finance-native schema in `wiki_prompts.py` EXTRACT prompt (7 arrays + fact_id + caps +
  overflow_facts). Full Pass-1 facts persist to `source_items.pass1_facts` (migration
  `k8s/migrations/002_add_pass1_facts.sql`; manual psql apply — migrations/ is gitignored
  per repo convention).
- Pass 3 Reflect & Verify (`_pass_reflect`, thinking ON, `allow_retry=False`): corrections
  applied programmatically per-section (unique-match only); bounded rewrite for high-priority
  gaps; heuristic `_verify_and_repair` is the fallback. Number normalization: `number_normalizer.py`
  (ambiguous "1,234"/"1.234" never guessed; VND only when explicit).
- Rollout/reprocess: `scripts/reprocess-wiki.py` (force-reprocess completed items, clears
  `_wiki_page_id` fast-path marker, bounded batch, audit). Snapshot retention 7 days.
  Wiki job timeout raised 1800s → 3600s (chunked canary with slow LLM API latency).
- Runbook: `docs/operations/wiki-extraction-v2-rollout.md` (migration-before-code, flags,
  canary gates, rollback).
- Canary (2026-08-23, `u45c_nVV0Sk`): chunk 1/8 alone extracted 17-20 numbers + 8-9 events
  vs 17 numbers / 5 claims for the whole video on the old single-pass pipeline.
