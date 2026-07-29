# Agent Notes — Kubernetes

## Cluster Context

```bash
kubectl config use-context kind-llm-wiki
kubectl -n llm-wiki get all
```

## Deployment Order

```bash
# 1. Global resources
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml

# 2. Storage / dependencies
kubectl apply -f k8s/postgres/
kubectl apply -f k8s/redis/
kubectl apply -f k8s/minio/

# 3. RBAC (must be applied before backend pods start)
kubectl apply -f k8s/backend/rbac.yaml

# 4. Application workloads
kubectl apply -f k8s/backend/
kubectl apply -f k8s/cpu-worker/
kubectl apply -f k8s/wiki-consumer/
kubectl apply -f k8s/frontend/
kubectl apply -f k8s/ollama/
kubectl apply -f k8s/telegram-bot/

# 5. Monitoring (after app pods are running)
kubectl apply -f k8s/monitoring/
# Or use the convenience script:
# ./scripts/deploy-monitoring.sh

# 6. Ingress
kubectl apply -f k8s/ingress.yaml
```

## Loading Images into kind

Images must be loaded into the kind node before the cluster can use `IfNotPresent`:

```bash
# Backend — only rebuild when pyproject.toml changes (hostPath handles code changes)
docker build -t 32_llm_wiki_clean_arch-backend:latest .
kind load docker-image 32_llm_wiki_clean_arch-backend:latest --name llm-wiki

# Frontend — rebuild for EVERY code change (production standalone, no hostPath)
cd frontend
docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest .
kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki
```

> `--network=host` is required for frontend builds in this environment so npm can reach the network.

## Backend RBAC is Required

`k8s/backend/rbac.yaml` grants the backend ServiceAccount permission to read `cronjobs` and `jobs`. The `/api/admin/cron-jobs` endpoint uses this to report real status. Without it, the endpoint silently degrades to DB-only status.

## Cron Job Status API

Endpoint: `GET /api/admin/cron-jobs`

Returned statuses by job type:

| job_type | status | meaning |
|----------|--------|---------|
| `kubernetes_cronjob` | `scheduled` | CronJob exists and is not suspended |
| `kubernetes_cronjob` | `running` | A child Job is currently active |
| `kubernetes_cronjob` | `error` | Most recent child Job failed |
| `kubernetes_cronjob` | `stopped` | CronJob exists but `spec.suspend: true` |
| `kubernetes_cronjob` | `not_found` | No matching CronJob in namespace |
| `background_task` | `running` | At least one worker heartbeat within 60s |
| `background_task` | `no_workers` | No recent worker heartbeats |
| all | `stopped` | `cron_jobs.enabled == false` |

The frontend maps these strings to badges in `frontend/components/admin/cron-jobs-panel.tsx`.

## Common Gotchas

- Backend deployment sets `serviceAccountName: backend-v2`. Do not remove it.
- `imagePullPolicy: IfNotPresent` is used everywhere. Load images into kind after each rebuild.
- The `youtube-daily-scan` CronJob triggers the backend via `wget` POST to `/api/admin/cron-jobs/youtube-daily-scan/start`.
- **Prometheus scrape annotations** (`prometheus.io/scrape: "true"`, `prometheus.io/port`, `prometheus.io/path`) must be present on pod templates for auto-discovery. Backend, cpu-worker, and wiki-consumer pods already have them.
- **Monitoring stack** (`k8s/monitoring/`) deploys Prometheus, Grafana, Loki, Promtail, and AlertManager. Expose UIs via `./scripts/monitoring-socat-forward.sh` (Grafana → :3100, Prometheus → :9090, AlertManager → :9093, Loki → :3200, Frontend → :3000, Backend → :8000).
- **Postgres/Redis/Ollama exporters** run as sidecar containers in their respective pods. Metrics flow through Prometheus pod annotations.

## Deploy After Code Changes

```
Changed backend code (.py)    → kubectl -n llm-wiki rollout restart deploy/backend-v2  (hostPath sync, just restart)
Changed frontend code (.tsx)  → docker build --network=host → kind load → kubectl rollout restart deploy/frontend
Changed pyproject.toml        → docker build → kind load → kubectl rollout restart deploy/backend-v2
Changed package.json          → docker build --network=host → kind load → kubectl rollout restart deploy/frontend
```

> **Backend hostPath caveat:** `uvicorn --reload` may not detect file changes through Docker-in-Docker (kind). If in doubt, restart the pod.

## Entity Graph API Notes

`GET /api/entity-graph` and `GET /api/cluster-expand` now return real data from the database:

- Entity nodes include `event_count` derived from `event_entity_links`.
- Edges are real `entity_relations` rows with `edge_type: "entity_relation"`.
- `/api/cluster-graph` only counts entities that have at least one relation (isolated entities are hidden from the cluster view).
- `/api/cluster-expand` limits the number of entities per request to keep the 3D graph responsive.

Frontend renders expanded cluster edges automatically because `toEntityGraph()` in `frontend/components/kg/kg-cluster-graph.tsx` already filters and displays `entity_relation` edges.
