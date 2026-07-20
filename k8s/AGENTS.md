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

# 5. Ingress
kubectl apply -f k8s/ingress.yaml
```

## Loading Images into kind

Images must be loaded into the kind node before the cluster can use `IfNotPresent`:
```bash
kind load docker-image 32_llm_wiki_clean_arch-backend:latest --name llm-wiki
kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki
```

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

## Entity Graph API Notes

`GET /api/entity-graph` and `GET /api/cluster-expand` now return real data from the database:

- Entity nodes include `event_count` derived from `event_entity_links`.
- Edges are real `entity_relations` rows with `edge_type: "entity_relation"`.
- `/api/cluster-graph` only counts entities that have at least one relation (isolated entities are hidden from the cluster view).
- `/api/cluster-expand` limits the number of entities per request to keep the 3D graph responsive.

Frontend renders expanded cluster edges automatically because `toEntityGraph()` in `frontend/components/kg/kg-cluster-graph.tsx` already filters and displays `entity_relation` edges.
