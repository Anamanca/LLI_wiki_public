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

## Verified State (2026-07-20)

- Frontend image: `32_llm_wiki_clean_arch-frontend:latest`
- Backend image: `32_llm_wiki_clean_arch-backend:latest`
- Cluster context: `kind-llm-wiki`
- Namespace: `llm-wiki`
- `/api/admin/cron-jobs` returns real status based on K8s CronJob/Job state and worker heartbeats.
