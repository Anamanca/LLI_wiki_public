# LLM Wiki — K3s/Kind Deployment

## Kiến trúc services

```
Namespace: llm-wiki

  postgres       (StatefulSet)  — PostgreSQL + pgvector, data persisted to /data/minio
  redis          (Deployment)   — Valkey 8, data persisted to /data/redis
  minio          (StatefulSet)  — S3-compatible object storage, data at /data/minio
  ollama         (Deployment)   — LLM inference, models at /data/ollama

  backend-v2     (Deployment)   — FastAPI backend, uvicorn --reload via /code/backend hostPath
  cpu-worker     (Deployment)   — CPU-bound worker, source via /code/backend hostPath
  wiki-consumer  (StatefulSet)  — Wiki ingestion consumer, source via /code/backend hostPath
  telegram-bot   (Deployment)   — Telegram bot, source via /code/telegram-bot hostPath

  frontend       (Deployment)   — Next.js 14 dev server, HMR via /code/frontend hostPath

Ingress (nginx): llm-wiki.local → /api → backend-v2:8000, / → frontend:3000
NodePort: frontend exposed on host:30080
```

## Prerequisites

- **Docker** — để build image và chạy kind cluster
- **kind** — Kubernetes-in-Docker (`go install sigs.k8s.io/kind@latest`)
- **kubectl** — để tương tác với cluster
- **nginx ingress controller** — cài riêng trong kind cluster

## Quick start — tạo cluster từ đầu

```bash
# 1. Build tất cả image
docker build -t 32_llm_wiki_clean_arch-backend:latest .
docker build -t 32_llm_wiki_clean_arch-frontend:latest ./frontend

# 2. Tạo kind cluster (extraMounts trong kind-config.yaml sẽ map hostPath vào node)
kind create cluster --config k8s/kind-config.yaml

# 3. Load image vào kind node (hoặc build trực tiếp trong kind)
kind load docker-image 32_llm_wiki_clean_arch-backend:latest --name llm-wiki
kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki

# 4. Cài nginx ingress controller
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx --for=condition=ready pod -l app.kubernetes.io/component=controller --timeout=90s

# 5. Deploy toàn bộ stack
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/

# 6. Kiểm tra
kubectl get pods -n llm-wiki
# Truy cập: http://llm-wiki.local:30080 (thêm 127.0.0.1 llm-wiki.local vào /etc/hosts)
```

## Development mode — thay đổi code không cần rebuild

### Cơ chế chung: kind extraMounts + hostPath volume

`kind-config.yaml` khai báo `extraMounts` để mount các thư mục source từ host vào trong kind node. Các deployment dùng `hostPath` volume trỏ tới những mount point đó (`/code/...`).

**Lưu ý quan trọng:** `extraMounts` trong kind chỉ có hiệu lực khi **tạo cluster**. Nếu thêm mount mới, phải `kind delete cluster` rồi tạo lại.

### Backend / Workers (uvicorn --reload)

Backend deployment đã mount `/code/backend` → `/code/backend` trong container và chạy `uvicorn --reload`. Sửa code Python → uvicorn tự detect và reload. Không cần rebuild image.

### Frontend (Next.js dev + HMR)

Frontend dùng cơ chế phức tạp hơn vì cần giữ `node_modules` từ image trong khi mount source code từ host:

```
volumes:
  source (hostPath: /code/frontend)  →  /app             ← source code live từ host
  deps   (emptyDir)                  →  /app/node_modules ← ghi đè node_modules từ host
```

**Tại sao cần emptyDir trick?** Nếu chỉ mount hostPath `/code/frontend` vào `/app`, host không có `node_modules` (đã bị `.dockerignore` exclude khi build image) → container sẽ thiếu dependencies.

**Cách hoạt động:**
1. `initContainer: copy-node-modules` copy `node_modules` từ image gốc vào emptyDir `/deps`
2. Container chính mount hostPath vào `/app`, xong mount emptyDir đè lên `/app/node_modules`
3. Kết quả: code từ host, dependencies từ image → `next dev` HMR hoạt động

### Workflow sửa code

```
Sửa frontend/app/page.tsx → next dev detect file change → HMR refresh trình duyệt
Sửa src/llm_wiki/...py   → uvicorn --reload detect → backend tự restart
```

Không cần build lại image hay restart pod cho cả backend lẫn frontend.

## Build lại image

Chỉ cần build lại khi thay đổi **dependencies**:

```bash
# Backend — khi sửa pyproject.toml (thêm/bớt package)
docker build -t 32_llm_wiki_clean_arch-backend:latest .

# Frontend — khi sửa package.json
docker build -t 32_llm_wiki_clean_arch-frontend:latest ./frontend

# Load lại vào kind và rollout restart
kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki
kubectl rollout restart deployment/frontend -n llm-wiki
```

## Cấu trúc file

```
k8s/
├── kind-config.yaml         # Kind cluster config — extraMounts cho tất cả hostPath
├── namespace.yaml           # Namespace llm-wiki
├── configmap.yaml           # Non-sensitive config
├── secret.yaml              # Secrets (DB URL, API keys, ...)
├── ingress.yaml             # Nginx ingress — llm-wiki.local
├── postgres/                # PostgreSQL + pgvector
├── redis/                   # Valkey
├── minio/                   # Object storage
├── ollama/                  # LLM inference
├── backend/                 # FastAPI backend
├── frontend/                # Next.js frontend
├── cpu-worker/              # CPU worker
├── wiki-consumer/           # Wiki ingestion
└── telegram-bot/            # Telegram bot
```

## Troubleshooting

### Pod frontend không start — `exec ./node_modules/.bin/next: no such file`

Image mới build nhưng chưa load vào kind. Chạy `kind load docker-image`.

### Sửa code frontend không thấy HMR

Mount point trong kind-config chưa đúng. Kiểm tra:
```bash
docker exec llm-wiki-control-plane ls /code/frontend
```
Nếu không có file nào → cần thêm `extraMounts` và **tạo lại cluster**.

### Pod frontend crash vì thiếu node_modules

Host không có `node_modules/` (đã bị .dockerignore exclude). Đảm bảo `initContainer: copy-node-modules` chạy thành công — nó copy từ image vào emptyDir trước khi container chính mount.

### "ImagePullBackOff" với image local

Image chưa được load vào kind node:
```bash
kind load docker-image <image-name>:latest --name llm-wiki
```

### Port 30080 đã được sử dụng

Process khác đang chiếm port. Tìm và kill: `sudo lsof -i :30080`

### Muốn truy cập từ máy khác trong mạng LAN

Sửa `extraPortMappings` trong kind-config, thêm `hostPort` và đảm bảo bind đúng interface. Hoặc dùng `kubectl port-forward`:
```bash
kubectl port-forward -n llm-wiki svc/frontend 3000:3000 --address 0.0.0.0
```
