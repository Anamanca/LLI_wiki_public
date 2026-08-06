# Agent Notes — Kubernetes

## Cluster Context

```bash
kubectl config use-context kind-llm-wiki
kubectl -n llm-wiki get all
```

Kind container name: `llm-wiki-control-plane` (run `docker ps` to confirm).

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

## HostPath Architecture — How Code Sync Works

Backend source code is mounted into pods via a hostPath chain:

```
Host (Linux)                       Kind node                      Pod container
/home/hieunt/32_LLM_wiki_clean_arch/src/
    │
    └─[kind extraMounts]──►  /code/backend-src/
                                   │
                                   └─[k8s hostPath volume]──►  /app/src/
```

`k8s/kind-config.yaml` defines the `extraMounts` that map host directories into the kind Docker container. Three deployments mount `/code/backend-src` → `/app/src`:

| Deployment | Image | Command | hostPath sync method |
|---|---|---|---|
| `backend-v2` | `backend:latest` | `uvicorn ... --reload` | `docker cp` → restart pod |
| `cpu-worker` | `worker:latest` | `python -m llm_wiki...cpu_worker` | `docker cp` → restart pod |
| `wiki-consumer` | `worker:latest` | `python -m llm_wiki...wiki_consumer` | `docker cp` → restart pod |

**Critical**: When you change `.py` files, you MUST copy them to the kind node first, THEN restart the pod. The kind node is the source of truth for the hostPath volume. A pod restart without copying files first will load stale code.

Other hostPath mounts (data only, not code):

| hostPath on kind node | Pod(s) | Purpose |
|---|---|---|
| `/data/postgres` | postgres | PG data |
| `/data/redis` | redis | Redis append-only file |
| `/data/minio` | minio | Object storage |
| `/data/ollama` | ollama | Model cache |
| `/data/chat-history` | backend-v2 | Chat history JSON files |
| `/data/transcripts` | cpu-worker | Downloaded transcripts |
| `/data/whisper-cache` | cpu-worker | HuggingFace model cache |
| `/code/telegram-bot` | telegram-bot | Telegram bot source |
| `/data/models/huggingface` | backend-v2 | Reranker model cache (NOT in kind-config — created at runtime) |

## Loading Images into kind

Images must be loaded into the kind node before the cluster can use `IfNotPresent`.

**Image names used in k8s manifests:**

| Image | Tag | Built from |
|---|---|---|
| `backend:latest` | bare name | `docker build --network=host -t backend:latest .` |
| `worker:latest` | bare name | `docker build --network=host -t worker:latest .` |
| `32_llm_wiki_clean_arch-frontend:latest` | prefixed | `docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest frontend/` |

> The frontend is the only image using the `32_llm_wiki_clean_arch-` prefix. Backend and worker images use bare names.

```bash
# Backend — rebuild ONLY when pyproject.toml or Dockerfile changes
docker build --network=host -t backend:latest .
kind load docker-image backend:latest --name llm-wiki

# Worker (cpu-worker + wiki-consumer share this image) — rebuild ONLY when pyproject.toml or Dockerfile changes
docker build --network=host -t worker:latest .
kind load docker-image worker:latest --name llm-wiki

# Frontend — rebuild for EVERY code change (production standalone, no hostPath)
docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest frontend/
kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki
```

> `--network=host` is required — the default Docker bridge cannot resolve external registries (`EAI_AGAIN`).

## Deploy After Code Changes

### Backend Python code (`.py` files in `src/`)

```
Step 1: Copy changed files to kind node via docker cp
        docker cp <host-file> llm-wiki-control-plane:/code/backend-src/<relative-path>

Step 2: Restart ALL pods that mount /code/backend-src
        kubectl -n llm-wiki rollout restart deploy/backend-v2
        kubectl -n llm-wiki rollout restart deploy/cpu-worker
        kubectl -n llm-wiki rollout restart deploy/wiki-consumer
```

Example for a single file fix:
```bash
docker cp src/llm_wiki/application/use_cases/query/pipeline.py \
  llm-wiki-control-plane:/code/backend-src/llm_wiki/application/use_cases/query/pipeline.py
kubectl -n llm-wiki rollout restart deploy/backend-v2
```

**Why not just restart?** Restarting the pod without `docker cp` first reloads stale code from the kind node's filesystem. The hostPath chain means the kind node is the source of truth — your local edits are NOT visible to pods until you copy to the kind node.

**Why restart all 3 pods?** `backend-v2`, `cpu-worker`, and `wiki-consumer` all mount the same `/code/backend-src` hostPath. If you change shared code (e.g., domain logic, repositories, datetime_utils), all 3 must be restarted to pick up the change. If you only change presentation-layer code (routes, schemas), restarting just `backend-v2` is sufficient.

**`uvicorn --reload` caveat:** May not detect file changes through Docker-in-Docker (kind). Always restart the pod to be safe.

### pyproject.toml or Dockerfile changes

```
docker build --network=host -t backend:latest .  (and/or worker:latest)
kind load docker-image backend:latest --name llm-wiki
kind load docker-image worker:latest --name llm-wiki
kubectl -n llm-wiki rollout restart deploy/backend-v2
kubectl -n llm-wiki rollout restart deploy/cpu-worker
kubectl -n llm-wiki rollout restart deploy/wiki-consumer
```

### Frontend code (`.tsx`, `.ts`, `.css` in `frontend/`)

```
docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest frontend/
kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki
kubectl -n llm-wiki rollout restart deploy/frontend
```

> Frontend has NO hostPath mount — it is fully self-contained in its Docker image (`output: 'standalone'`).

### Convenience shortcut

Use `scripts/sync-and-test.sh --backend` to sync + test. But note: this script uses `kubectl cp` directly into the pod (which also writes through to the hostPath). It only syncs a hardcoded list of files — you may need to add new files to the `files` array in `sync_backend()`.

## Quick Reference: When to Rebuild vs When to Sync

| What changed | Action | Pods to restart |
|---|---|---|
| `src/llm_wiki/**/*.py` | `docker cp` → kind node → restart | backend-v2, cpu-worker, wiki-consumer |
| `pyproject.toml` | `docker build` + `kind load` → restart | backend-v2, cpu-worker, wiki-consumer |
| `Dockerfile` | `docker build` + `kind load` → restart | backend-v2 and/or cpu-worker + wiki-consumer |
| `frontend/**/*.{tsx,ts,css}` | `docker build` + `kind load` → restart | frontend |
| `frontend/package.json` | `docker build` + `kind load` → restart | frontend |
| `k8s/**/*.yaml` | `kubectl apply` → depends | as needed |

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
- **Monitoring stack** (`k8s/monitoring/`) deploys Prometheus, Grafana, Loki, Promtail, and AlertManager. Prometheus/Grafana/Alertmanager data is ephemeral (`emptyDir`) — lost on pod restart. Loki uses a PVC (1Gi).
- Expose UIs via `./scripts/monitoring-socat-forward.sh` (Grafana → :3100, Prometheus → :9090, AlertManager → :9093, Loki → :3200, Frontend → :3000, Backend → :8000).
- **Postgres/Redis/Ollama exporters** run as sidecar containers in their respective pods. Metrics flow through Prometheus pod annotations.
- `deploy-k8s.sh` is for K3s ONLY — it uses `sudo k3s ctr images import`. Do NOT use it with Kind.

## Entity Graph API Notes

`GET /api/entity-graph` and `GET /api/cluster-expand` now return real data from the database:

- Entity nodes include `event_count` derived from `event_entity_links`.
- Edges are real `entity_relations` rows with `edge_type: "entity_relation"`.
- `/api/cluster-graph` only counts entities that have at least one relation (isolated entities are hidden from the cluster view).
- `/api/cluster-expand` limits the number of entities per request to keep the 3D graph responsive.

Frontend renders expanded cluster edges automatically because `toEntityGraph()` in `frontend/components/kg/kg-cluster-graph.tsx` already filters and displays `entity_relation` edges.
