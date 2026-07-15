# Agent Guidelines — K8s Deployment

## Target
- Local/dev: `kind` cluster named `llm-wiki`.
- Remote: K3s cluster.
- Namespace: `llm-wiki`.

## Services
| Service | Role | Data path / image source |
|---------|------|--------------------------|
| `postgres` | PostgreSQL + pgvector | `/data/postgres` |
| `redis` | Valkey 8 cache | `/data/redis` |
| `minio` | S3-compatible object storage | `/data/minio` |
| `ollama` | Embedding / LLM inference | `/data/ollama` |
| `backend-v2` | FastAPI backend | Docker image `32_llm_wiki_clean_arch-backend:latest` (no hostPath mount) |
| `frontend` | Next.js dev server | hostPath `/code/frontend` + image `32_llm_wiki_clean_arch-frontend:latest` for `node_modules` |
| `cpu-worker` | CPU-bound worker | hostPath `/code/backend` (legacy project path) |
| `wiki-consumer` | Wiki ingestion consumer | hostPath `/code/backend` (legacy project path) |
| `telegram-bot` | Telegram bot | hostPath `/code/telegram-bot` |

> **Note:** `backend-v2` runs entirely from its Docker image. Editing source code on the host does **not** affect it unless a new image is built and loaded. Only `cpu-worker` and `wiki-consumer` pick up live host-source changes via `uvicorn --reload` / hostPath.

## Ingress
- Host: `llm-wiki.local` (add `127.0.0.1 llm-wiki.local` to `/etc/hosts`).
- `/api/*` → `backend-v2:8000`
- `/` → `frontend:3000`
- NodePort: `30080` also exposed.

## Dev Mode (no rebuild)
- `kind-config.yaml` mounts host dirs into kind node via `extraMounts`.
- `frontend` uses `initContainer` copying `node_modules` from image into `emptyDir`, then mounts host source + emptyDir over `/app/node_modules` for HMR.
- `cpu-worker` and `wiki-consumer` mount `hostPath` `/code/backend` and run code from there (legacy project path).
- `backend-v2` runs from its image with `uvicorn --reload`; live source edits require either `kubectl cp` into the pod (lost on restart) or rebuilding the image.
- **Important:** `extraMounts` only apply when creating cluster. Adding new mounts requires `kind delete cluster` and recreate.

## Common Commands
```bash
# Create kind cluster
kind create cluster --config k8s/kind-config.yaml

# Install nginx ingress
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

# Deploy full stack
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/

# Check pods
kubectl get pods -n llm-wiki

# Port-forward for local dev
kubectl port-forward -n llm-wiki svc/postgres 5432:5432
kubectl port-forward -n llm-wiki svc/redis   6379:6379
kubectl port-forward -n llm-wiki svc/ollama  11434:11434
kubectl port-forward -n llm-wiki svc/minio   9000:9000

# Deploy helpers
./scripts/deploy-k8s.sh      # K3s build & deploy (uses k3s ctr; not for kind)
./scripts/sync-and-test.sh   # sync changed files + run tests (also sets up port-forward)
./scripts/test-apis.sh       # run contract tests (requires backend reachable)

# For kind, use the manual Build/Load/Restart flow above instead of deploy-k8s.sh.
```

## Build Images
```bash
# Backend
docker build -t 32_llm_wiki_clean_arch-backend:latest .

# Frontend
docker build -t 32_llm_wiki_clean_arch-frontend:latest ./frontend
```

### DNS issue during `apt-get update` in Docker build
If the build hangs at `apt-get update` with lines like `Ign:1 http://deb.debian.org/debian trixie InRelease`, Docker's default DNS cannot resolve Debian mirrors. Fix by building with the host network:

```bash
docker build --network host -t 32_llm_wiki_clean_arch-backend:latest .
docker build --network host -t 32_llm_wiki_clean_arch-frontend:latest ./frontend
```

## Load Images into kind
```bash
kind load docker-image 32_llm_wiki_clean_arch-backend:latest --name llm-wiki
kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki
```

## Redeploy on kind
```bash
# Restart deployments so pods pick up the new image
kubectl rollout restart deployment/backend-v2 -n llm-wiki
kubectl rollout restart deployment/frontend -n llm-wiki

# Wait for rollout
kubectl rollout status deployment/backend-v2 -n llm-wiki --timeout=120s
kubectl rollout status deployment/frontend -n llm-wiki --timeout=120s
```

## Troubleshooting
- `ImagePullBackOff` → image not loaded into kind node; run `kind load docker-image`.
- `ErrImageNeverPull` / `InvalidImageName` → check the image tag and `imagePullPolicy`.
- Backend returns old API shapes (e.g. missing `db`, `citations`, `sources` wrapper) → `backend-v2` is running an old image. Rebuild + `kind load` + `kubectl rollout restart deployment/backend-v2`.
- Frontend crash `exec ./node_modules/.bin/next: no such file` → rebuild + load frontend image, or initContainer failed.
- No HMR → check `docker exec llm-wiki-control-plane ls /code/frontend`.
- Port 30080 busy → `sudo lsof -i :30080` and kill, or change `extraPortMappings`.
- Docker build hangs at `apt-get update` → use `--network host` (see Build Images).

## Adding a New Service
1. Create manifest dir under `k8s/<service>/`.
2. Add `Deployment` + `Service`.
3. If needs host source: add `extraMounts` in `kind-config.yaml` **before** cluster create.
4. Add env vars to `k8s/configmap.yaml` or `k8s/secret.yaml`.
5. Update this file and root `AGENTS.md`.
