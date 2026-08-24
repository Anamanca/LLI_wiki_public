# LLM Wiki — K3s/Kind Deployment

## Kiến trúc services

```
Namespace: llm-wiki

  postgres       (StatefulSet)  — PostgreSQL + pgvector, data persisted to /data/postgres
  redis          (Deployment)   — Valkey 8, data persisted to /data/redis
  minio          (StatefulSet)  — S3-compatible object storage, data at /data/minio
  ollama         (Deployment)   — LLM inference, models at /data/ollama

  backend-v2     (Deployment)   — FastAPI backend, uvicorn --reload via /code/backend-src hostPath
  cpu-worker     (StatefulSet)  — CPU-bound worker, auto WORKER_ID from hostname ordinal, independent scale
  wiki-consumer  (StatefulSet)  — Wiki ingestion consumer, source via /code/backend-src hostPath
  telegram-bot   (Deployment)   — Telegram bot, source via /code/telegram-bot hostPath

  frontend       (Deployment)   — Next.js production build (standalone, node server.js)

NodePort: frontend :30080 (toàn app — proxy /api nội bộ), backend :30081 (API trực tiếp)
```

## Scaling cpu-worker

cpu-worker chạy dưới dạng **StatefulSet** với `replicas: 1` mặc định. Muốn tăng/giảm số worker:

```bash
# Scale lên 2 worker
kubectl scale statefulset cpu-worker -n llm-wiki --replicas=2

# Scale về 1 worker
kubectl scale statefulset cpu-worker -n llm-wiki --replicas=1
```

### Cơ chế tự động

| Vấn đề | Cách xử lý |
|--------|-----------|
| **WORKER_ID trùng** | StatefulSet pod name: `cpu-worker-0`, `cpu-worker-1` → WORKER_ID = 1 + ordinal (từ hostname) → ID duy nhất |
| **Xung đột claim job** | PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` — mỗi worker claim job khác nhau, không race |
| **Orphan job (scale down)** | `claim_job()` reclaim job stuck `processing` > 30 phút — worker cũ đã bị kill, job được claim lại |
| **Worker health** | Worker heartbeat ghi Postgres + `scripts/healthcheck.sh` query trực tiếp (monitoring stack đã gỡ 2026-08-23) |
| **Giao tiếp service** | Tất cả qua DNS nội bộ `*.llm-wiki.svc.cluster.local` — không phụ thuộc pod IP |

### Kiến trúc job queue

```
backend-v2 (API, :8000)
  │  POST /admin/cron-jobs/{id}/start
  │  → poll_channel() → INSERT SourceItem(status='pending')
  ▼
PostgreSQL (bảng source_items)
  │  ┌─── SKIP LOCKED claim (cpu-worker-0, WORKER_ID=1)
  │  └─── SKIP LOCKED claim (cpu-worker-1, WORKER_ID=2)
  ▼
cpu-worker-0 / cpu-worker-1
  │  extract → classify → embed
  │  → push_wiki_job() vào Redis
  ▼
Redis (queue wiki)
  ▼
wiki-consumer (StatefulSet, replicas:2)
  │  wiki integration
```

### Tách API khỏi worker

Trước đây cpu-worker deployment cũ chứa cả container `api` (uvicorn :8100) và `cpu-worker`. Container `api` này không hề "điều khiển" worker — nó chỉ là 1 instance FastAPI dư thừa, vì backend-v2 đã chạy API riêng trên port 8000.

Khi refactor sang StatefulSet, container `api` đã được loại bỏ. API vẫn chạy bình thường qua `backend-v2` deployment — scale API và scale worker độc lập với nhau.

## Prerequisites

- **Docker** — để build image và chạy kind cluster
- **kind** — Kubernetes-in-Docker (`go install sigs.k8s.io/kind@latest`)
- **kubectl** — để tương tác với cluster

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

# 4. Deploy toàn bộ stack
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/

# 5. Kiểm tra
kubectl get pods -n llm-wiki
./scripts/healthcheck.sh
# Truy cập: http://localhost:30080 (frontend) / http://localhost:30081/api/health (backend)
# Từ máy khác qua Tailscale: http://100.115.181.93:30080
```

## Development mode — thay đổi code không cần rebuild

### Cơ chế chung: kind extraMounts + hostPath volume

`kind-config.yaml` khai báo `extraMounts` để mount các thư mục source từ host vào trong kind node. Các deployment dùng `hostPath` volume trỏ tới những mount point đó.

**Lưu ý quan trọng:** `extraMounts` trong kind chỉ có hiệu lực khi **tạo cluster**. Nếu thêm mount mới, phải `kind delete cluster` rồi tạo lại.

### Backend / Workers (hostPath + uvicorn --reload) ✅

Backend deployment mount `/code/backend-src` (kind node) → `/app/src` (container), chạy `uvicorn --reload`. Sửa code Python → uvicorn tự detect và reload.

**Caveat:** hostPath mounts qua Docker-in-Docker (kind) không phải lúc nào cũng trigger inotify events reliably. Nếu uvicorn không tự reload, restart pod:

```bash
kubectl -n llm-wiki rollout restart deployment/backend-v2
```

Không cần build lại backend image khi chỉ sửa code Python.

### Frontend — PHẢI BUILD LẠI IMAGE 🔴

Khác với backend, **frontend chạy ở production mode** (`node server.js` từ standalone build), không dùng hostPath. Mỗi lần sửa code frontend, phải build lại image và load vào kind:

```bash
cd frontend
docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest .
kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki
kubectl -n llm-wiki rollout restart deployment/frontend
```

> **Tại sao không dùng dev mode?** Frontend dev mode (`next dev` + HMR) yêu cầu mount source code live qua hostPath và giữ `node_modules` từ image. Cơ chế này chưa được implement trong deployment hiện tại (cần emptyDir trick + initContainer). Nếu cần HMR, tham khảo thiết kế ở cuối mục này.

### Workflow sửa code

```
Sửa src/llm_wiki/...py      → uvicorn --reload detect (hoặc restart pod nếu không reload)
Sửa frontend/**/*.tsx        → build image → load vào kind → restart pod
```

### Kế hoạch tương lai: Frontend dev mode với HMR

Để có HMR cho frontend, cần implement cơ chế sau trong deployment:

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

## Build lại image

### Backend — khi thay đổi dependencies

```bash
# Khi sửa pyproject.toml (thêm/bớt package)
docker build -t 32_llm_wiki_clean_arch-backend:latest .
kind load docker-image 32_llm_wiki_clean_arch-backend:latest --name llm-wiki
kubectl rollout restart deployment/backend-v2 -n llm-wiki
```

Sửa code Python thông thường không cần build lại — hostPath + uvicorn --reload đã xử lý.

### Frontend — mỗi lần sửa code

```bash
cd frontend
docker build --network=host -t 32_llm_wiki_clean_arch-frontend:latest .
kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki
kubectl -n llm-wiki rollout restart deployment/frontend
```

> **`--network=host`** bắt buộc khi build frontend trong môi trường này để npm có thể truy cập network.

## Cấu trúc file

```
k8s/
├── kind-config.yaml         # Kind cluster config — extraMounts cho tất cả hostPath
├── namespace.yaml           # Namespace llm-wiki
├── configmap.yaml           # Non-sensitive config
├── secret.yaml              # Secrets (DB URL, API keys, ...)
├── postgres/                # PostgreSQL + pgvector
├── redis/                   # Valkey
├── minio/                   # Object storage
├── ollama/                  # LLM inference + ollama_exporter sidecar
├── backend/                 # FastAPI backend
├── frontend/                # Next.js frontend
├── cpu-worker/              # CPU worker
├── wiki-consumer/           # Wiki ingestion
└── telegram-bot/            # Telegram bot
```

## Troubleshooting

### Pod frontend không start — `exec ./node_modules/.bin/next: no such file`

Image mới build nhưng chưa load vào kind. Chạy `kind load docker-image`.

### "ImagePullBackOff" với image local

Image chưa được load vào kind node:
```bash
kind load docker-image <image-name>:latest --name llm-wiki
```

### Sửa code backend không thấy thay đổi

uvicorn --reload không detect được file change qua hostPath (inotify không hoạt động qua Docker-in-Docker). Restart pod:
```bash
kubectl -n llm-wiki rollout restart deployment/backend-v2
```

### Port 30080 đã được sử dụng

Process khác đang chiếm port. Tìm và kill: `sudo lsof -i :30080`

### Muốn truy cập từ máy khác trong mạng LAN / Tailscale

Frontend/backend là **NodePort services**; `kind-config.yaml` map host port 30080/30081 → kind node → reachable trực tiếp từ localhost và mọi thiết bị Tailscale. **Không cần socat / kubectl port-forward.**

```
Host (0.0.0.0)                      Kind node                   Pod
──────                             ─────────                   ───
localhost:30080 ──► docker-proxy ──► NodePort 30080 ──► frontend svc :3000
100.115.181.93:30080 (Tailscale) ▲
```

| Service | URL |
|---------|-----|
| Frontend (toàn app) | `http://localhost:30080` / `http://100.115.181.93:30080` |
| Backend API | `http://localhost:30081/api/health` |

> **Lưu ý:** 2 systemd service forward-port cũ đã disable (2026-08-23): `llm-wiki-socat-forward.service` (user) và `k8s-frontend-portforward.service` (system). Nếu sau reboot port 30080 không lên, kiểm tra chúng không bị enable lại.

### Backend pod báo "No module named..." sau khi thêm file mới

hostPath mount source từ host, file mới xuất hiện ngay. Nhưng nếu thêm package mới (pyproject.toml), phải build lại image.

### Xem log backend để debug lỗi query/stream

```bash
kubectl -n llm-wiki logs -f deploy/backend-v2 | grep -E 'STREAM|error|WARNING'
```

---

# kubectl Command Reference (cho người mới học K8s)

Mục này tổng hợp các lệnh `kubectl` thường dùng, kèm giải thích ý nghĩa từng cột output. Tất cả ví dụ dùng namespace `llm-wiki` và context `kind-llm-wiki`, nhưng có thể thay bằng namespace/context của bạn.

## Context & Namespace

```bash
# Liệt kê tất cả context đang có
kubectl config get-contexts

# Chuyển context (mỗi context = 1 cluster)
kubectl config use-context kind-llm-wiki

# Xem context hiện tại
kubectl config current-context

# Liệt kê tất cả namespace
kubectl get namespaces
# hoặc viết tắt:
kubectl get ns

# Mặc định namespace (dùng -n để chỉ định, nếu không dùng -n thì là namespace "default")
kubectl get pods -n llm-wiki

# Set namespace mặc định cho context hiện tại (đỡ phải gõ -n mỗi lần)
kubectl config set-context --current --namespace=llm-wiki
```

---

## Pod — đơn vị nhỏ nhất trong K8s

Pod chứa 1 hoặc nhiều container, chia sẻ network namespace và storage volumes.

### Xem danh sách pod

```bash
# Cơ bản
kubectl -n llm-wiki get pods

# Xem rộng hơn (thêm IP, node, nominated node)
kubectl -n llm-wiki get pods -o wide

# Xem tất cả pod trên mọi namespace
kubectl get pods --all-namespaces
# hoặc viết tắt:
kubectl get pods -A

# Watch pod (real-time, Ctrl+C để thoát)
kubectl -n llm-wiki get pods -w

# Lọc pod theo label
kubectl -n llm-wiki get pods -l app=frontend

# Sort theo thời gian tạo (mới nhất trên cùng)
kubectl -n llm-wiki get pods --sort-by=.metadata.creationTimestamp
```

### Ý nghĩa các cột trong `get pods`

| Cột | Ý nghĩa |
|------|---------|
| **NAME** | Tên pod. Nếu do Deployment tạo: `<deploy>-<rs-hash>-<pod-hash>`. Nếu do StatefulSet: `<sts-name>-<số-thứ-tự>` (vd `postgres-0`). |
| **READY** | `sẵn-sàng/tổng`. `2/2` = cả 2 container ready. `0/1` = chưa sẵn sàng (đang khởi động, probe fail). `1/2` = 1 container ready, 1 chưa. |
| **STATUS** | `Running` = đang chạy bình thường. `Pending` = đang chờ node scheduler hoặc pull image. `Completed` = job/pod đã chạy xong thành công. `Failed` = tất cả container đã dừng, ít nhất 1 container exit với lỗi. `CrashLoopBackOff` = container crash liên tục, K8s đang chờ rồi restart lại. `ImagePullBackOff` = không pull được image. `ErrImagePull` = image sai tên/không tồn tại. `Terminating` = đang bị xóa. |
| **RESTARTS** | Số lần container bị restart. Số cao bất thường → có thể OOM (Out of Memory), lỗi code, hoặc probe fail. |
| **AGE** | Thời gian pod đã tồn tại. `26h` = 26 giờ, `14d` = 14 ngày, `102m` = 102 phút. |

### Chi tiết pod

```bash
# Xem mọi thông tin chi tiết của pod (events, containers, volumes, conditions, ...)
kubectl -n llm-wiki describe pod <tên-pod>

# Đặc biệt quan trọng: phần Events cuối output — cho biết pod đã trải qua những gì
# (pull image, start container, probe fail, OOM kill, ...)
```

### Logs

```bash
# Xem log pod (stdout/stderr)
kubectl -n llm-wiki logs <tên-pod>

# Pod có nhiều container → phải chỉ định container
kubectl -n llm-wiki logs <tên-pod> -c <tên-container>

# Follow log real-time (Ctrl+C để thoát)
kubectl -n llm-wiki logs -f <tên-pod>

# Xem log gần đây (vd 100 dòng cuối)
kubectl -n llm-wiki logs --tail=100 <tên-pod>

# Xem log của pod đã crash/terminated trước đó
kubectl -n llm-wiki logs <tên-pod> --previous

# Xem log của tất cả pod có label app=frontend
kubectl -n llm-wiki logs -l app=frontend --all-containers=true
```

### Exec vào pod

```bash
# Mở shell bash trong container (nếu có bash)
kubectl -n llm-wiki exec -it <tên-pod> -- bash

# Pod có nhiều container → chỉ định container
kubectl -n llm-wiki exec -it <tên-pod> -c <tên-container> -- bash

# Nếu container không có bash, thử sh
kubectl -n llm-wiki exec -it <tên-pod> -- sh

# Chạy 1 lệnh đơn, không cần interactive
kubectl -n llm-wiki exec <tên-pod> -- ls /app
kubectl -n llm-wiki exec <tên-pod> -- env    # xem biến môi trường
kubectl -n llm-wiki exec <tên-pod> -- cat /etc/hosts
```

### Port forward (truy cập pod/service từ local)

```bash
# Forward port từ local vào pod
kubectl -n llm-wiki port-forward pod/<tên-pod> 8080:8000
# Giờ mở browser http://localhost:8080 là tới port 8000 của pod

# Forward vào service (ổn định hơn, không phụ thuộc pod cụ thể)
kubectl -n llm-wiki port-forward svc/backend-v2 8080:8000

# Cho phép truy cập từ máy khác trong LAN
kubectl -n llm-wiki port-forward svc/frontend 3000:3000 --address 0.0.0.0
```

### Xóa pod

```bash
# Xóa 1 pod (Deployment sẽ tự tạo lại pod mới)
kubectl -n llm-wiki delete pod <tên-pod>

# Xóa pod không chờ (force)
kubectl -n llm-wiki delete pod <tên-pod> --grace-period=0 --force

# Xóa tất cả pod thuộc 1 label
kubectl -n llm-wiki delete pod -l app=frontend
```

---

## Deployment — quản lý pod, hỗ trợ rolling update & rollback

Deployment quản lý ReplicaSet, ReplicaSet quản lý Pod. Đây là cách phổ biến nhất để chạy stateless app.

### Xem danh sách deployment

```bash
kubectl -n llm-wiki get deployments
# viết tắt:
kubectl -n llm-wiki get deploy

# Xem rộng
kubectl -n llm-wiki get deploy -o wide
```

### Ý nghĩa các cột trong `get deploy`

| Cột | Ý nghĩa |
|------|---------|
| **READY** | `sẵn-sàng/mong-muốn`. `1/1` = đủ pod. `0/0` = deployment bị scale về 0 (đang tắt). |
| **UP-TO-DATE** | Số pod đang chạy phiên bản template mới nhất. Trong lúc rolling update, số này tăng dần. |
| **AVAILABLE** | Số pod đã ready ít nhất `minReadySeconds` giây. Đây mới là số pod thực sự nhận traffic. |

### Quản lý deployment

```bash
# Scale deployment (tăng/giảm số pod)
kubectl -n llm-wiki scale deployment/frontend --replicas=3
kubectl -n llm-wiki scale deployment/backend --replicas=0   # tắt hẳn

# Restart deployment (tạo pod mới, pod cũ terminate dần - rolling restart)
kubectl -n llm-wiki rollout restart deployment/frontend

# Xem trạng thái rollout hiện tại
kubectl -n llm-wiki rollout status deployment/frontend

# Pause/resume rollout (nếu muốn can thiệp giữa chừng khi rolling update)
kubectl -n llm-wiki rollout pause deployment/frontend
kubectl -n llm-wiki rollout resume deployment/frontend

# Sửa trực tiếp deployment (mở editor)
kubectl -n llm-wiki edit deployment/frontend
```

### Rollout history & rollback

```bash
# Xem lịch sử rollout (mỗi lần update = 1 revision)
kubectl -n llm-wiki rollout history deployment/frontend

# Xem chi tiết 1 revision cụ thể (xem image, config của revision đó)
kubectl -n llm-wiki rollout history deployment/frontend --revision=23

# Rollback về revision trước đó
kubectl -n llm-wiki rollout undo deployment/frontend

# Rollback về 1 revision cụ thể
kubectl -n llm-wiki rollout undo deployment/frontend --to-revision=20
```

### Set image cho deployment (update container image)

```bash
kubectl -n llm-wiki set image deployment/frontend \
  frontend=32_llm_wiki_clean_arch-frontend:latest
```

---

## ReplicaSet — "bản sao" của pod template, do Deployment tự tạo

**Bạn hiếm khi phải thao tác trực tiếp với ReplicaSet.** Deployment sẽ tự quản lý.

### Xem ReplicaSet

```bash
kubectl -n llm-wiki get replicasets
# viết tắt:
kubectl -n llm-wiki get rs

# Xem rộng (thêm image, labels)
kubectl -n llm-wiki get rs -o wide
```

### Ý nghĩa các cột và cơ chế

| Cột | Ý nghĩa |
|------|---------|
| **DESIRED** | Số pod mong muốn. RS active luôn có DESIRED = số pod đang chạy. RS cũ có DESIRED = 0. |
| **CURRENT** | Số pod đang tồn tại (chưa tính trạng thái ready). |
| **READY** | Số pod đã ready. |

### Tại sao có nhiều ReplicaSet?

Mỗi lần bạn **thay đổi PodTemplate** (image, env, resource limits, probes...), K8s:
1. Tạo ReplicaSet **mới** với `pod-template-hash` mới
2. Scale dần pod mới lên, scale pod cũ xuống 0
3. Giữ ReplicaSet cũ (DESIRED=0) để **rollback**

Tên ReplicaSet = `<deployment-name>-<pod-template-hash>`. Hash là hash của toàn bộ PodTemplate, **không phải random** — cùng template thì cùng hash, thay đổi 1 dòng là hash khác.

```bash
# Số RS cũ được giữ = revisionHistoryLimit (mặc định 10)
kubectl -n llm-wiki get deploy -o json | jq '.items[].spec.revisionHistoryLimit'

# Xóa RS cũ thủ công (nếu chắc chắn không rollback)
kubectl -n llm-wiki delete rs -l app=frontend --field-selector status.replicas=0

# Giảm revisionHistoryLimit trong deployment để tự động dọn
kubectl -n llm-wiki patch deployment/frontend -p '{"spec":{"revisionHistoryLimit":3}}'
```

---

## Service — điểm truy cập mạng ổn định cho pod

Pod có IP thay đổi khi bị xóa/tạo lại. Service cung cấp 1 IP + DNS name ổn định.

### Xem danh sách service

```bash
kubectl -n llm-wiki get services
# viết tắt:
kubectl -n llm-wiki get svc

# Xem rộng
kubectl -n llm-wiki get svc -o wide
```

### Ý nghĩa các cột trong `get svc`

| Cột | Ý nghĩa |
|------|---------|
| **TYPE** | `ClusterIP` (mặc định): chỉ truy cập trong cluster. `NodePort`: mở port trên node, truy cập từ ngoài qua `<node-ip>:<nodePort>`. `LoadBalancer`: dùng cloud LB bên ngoài (GCP, AWS). |
| **CLUSTER-IP** | IP nội bộ do K8s cấp (dải `10.96.x.x`). IP này không thay đổi trừ khi xóa service. |
| **EXTERNAL-IP** | IP external (chỉ có ý nghĩa với LoadBalancer). `<none>` là bình thường với ClusterIP/NodePort. |
| **PORT(S)** | `8000:30081/TCP` = port service `8000` ánh xạ ra nodePort `30081` trên mọi node. |

### DNS trong cluster

Mỗi service có 1 DNS name nội bộ:
```
<tên-service>.<namespace>.svc.cluster.local
```
Ví dụ: `backend-v2.llm-wiki.svc.cluster.local:8000`

```bash
# Xem endpoints (IP pod thực tế mà service forward đến)
kubectl -n llm-wiki get endpoints
# hoặc:
kubectl -n llm-wiki describe svc/backend-v2   # xem phần Endpoints

# Xóa pod, endpoint tự cập nhật — đây là điểm mạnh của Service
```

---

## StatefulSet — giống Deployment nhưng cho app có state (DB, queue)

StatefulSet giữ:
- Tên pod ổn định: `postgres-0`, `postgres-1` (không phải hash random)
- Thứ tự tạo/xóa pod (tuần tự)
- Mỗi pod có PersistentVolumeClaim riêng (dữ liệu không bị mất khi pod restart)

```bash
kubectl -n llm-wiki get statefulsets
# viết tắt:
kubectl -n llm-wiki get sts

# Xem chi tiết
kubectl -n llm-wiki describe sts/postgres
```

### Ý nghĩa các cột

| Cột | Ý nghĩa |
|------|---------|
| **READY** | `1/1` = 1 pod ready trên tổng 1 mong muốn. |

---

## DaemonSet — chạy đúng 1 pod trên mỗi node

Dùng cho: log collector, monitoring agent, storage daemon.

```bash
kubectl -n llm-wiki get daemonsets
# viết tắt:
kubectl -n llm-wiki get ds
```

Cluster bạn có 1 node (kind) → `DESIRED=1, READY=1`. Nếu thêm node, tự động chạy thêm pod trên node mới.

---

## CronJob & Job — chạy task theo lịch hoặc 1 lần

### Xem

```bash
# CronJob
kubectl -n llm-wiki get cronjobs
# viết tắt:
kubectl -n llm-wiki get cj

# Job (do CronJob sinh ra hoặc tạo thủ công)
kubectl -n llm-wiki get jobs

# Xem tất cả job của 1 CronJob
kubectl -n llm-wiki get jobs -l app=postgres-backup
```

### Ý nghĩa các cột trong `get cronjobs`

| Cột | Ý nghĩa |
|------|---------|
| **SCHEDULE** | Cron expression: `0 0 * * *` = chạy lúc 00:00 mỗi ngày. `*/5 * * * *` = mỗi 5 phút. |
| **SUSPEND** | `False` = đang hoạt động. `True` = bị tạm dừng. |
| **ACTIVE** | Số job đang chạy. `0` = không có job nào đang chạy. |
| **LAST SCHEDULE** | Thời gian job được lên lịch gần nhất. |

### Ý nghĩa các cột trong `get jobs`

| Cột | Ý nghĩa |
|------|---------|
| **STATUS** | `Complete` = job thành công. `Failed` = job failed. `Running` = đang chạy. |
| **COMPLETIONS** | `1/1` = hoàn thành 1/1 lần. `0/1` = chưa hoàn thành. |
| **DURATION** | Thời gian job đã chạy. |

### Thao tác với CronJob/Job

```bash
# Tạo job thủ công từ CronJob (trigger ngay, không chờ schedule)
kubectl -n llm-wiki create job --from=cronjob/postgres-backup manual-backup-1

# Xem log của job (job đã completed vẫn xem được log)
kubectl -n llm-wiki logs job/postgres-backup-29746080

# Suspend/resume CronJob
kubectl -n llm-wiki patch cronjob/youtube-daily-scan -p '{"spec":{"suspend":true}}'
kubectl -n llm-wiki patch cronjob/youtube-daily-scan -p '{"spec":{"suspend":false}}'

# Xóa job cũ đã completed/failed để dọn dẹp
kubectl -n llm-wiki delete job postgres-backup-29744640

# CronJob tự giữ 3 job thành công + 1 job thất bại (mặc định)
# Cấu hình trong spec:
#   successfulJobsHistoryLimit: 3
#   failedJobsHistoryLimit: 1

# Xóa tất cả job đã completed
kubectl -n llm-wiki delete jobs --field-selector status.successful=1

# Xóa tất cả job đã failed
kubectl -n llm-wiki delete jobs --field-selector status.successful=0
```

---

## ConfigMap & Secret — tách config ra khỏi code

```bash
# Xem ConfigMap
kubectl -n llm-wiki get configmaps
kubectl -n llm-wiki get cm          # viết tắt
kubectl -n llm-wiki describe cm/app-config

# Xem Secret
kubectl -n llm-wiki get secrets
kubectl -n llm-wiki describe secret/db-credentials

# Xem nội dung secret (value đã base64 encode)
kubectl -n llm-wiki get secret/db-credentials -o jsonpath='{.data}'

# Giải mã 1 key trong secret
kubectl -n llm-wiki get secret/db-credentials -o jsonpath='{.data.DATABASE_URL}' | base64 -d

# Tạo ConfigMap từ file
kubectl -n llm-wiki create configmap my-config --from-file=config.ini

# Tạo ConfigMap từ literal value
kubectl -n llm-wiki create configmap my-config --from-literal=KEY1=value1 --from-literal=KEY2=value2
```

---

## Resource & Node — xem tài nguyên cluster

```bash
# Xem tài nguyên pod đang dùng (CPU/Memory thực tế)
kubectl -n llm-wiki top pods

# Xem tài nguyên node
kubectl top nodes

# Xem chi tiết node
kubectl describe node

# Xem danh sách node
kubectl get nodes -o wide
```

---

## Events — timeline của mọi thứ xảy ra

Events là log tập trung của K8s, ghi lại mọi hành động: pod scheduled, image pulled, container started, probe failed, OOM killed...

```bash
# Xem tất cả events trong namespace (mới nhất cuối cùng)
kubectl -n llm-wiki get events

# Sắp xếp events theo thời gian
kubectl -n llm-wiki get events --sort-by=.metadata.creationTimestamp

# Xem events của toàn cluster
kubectl get events -A --sort-by=.metadata.creationTimestamp | tail -50

# Watch events real-time
kubectl -n llm-wiki get events -w

# Chỉ xem events warning (cảnh báo)
kubectl -n llm-wiki get events --field-selector type=Warning
```

---

## Labels & Selectors — gắn tag và lọc tài nguyên

```bash
# Xem labels của pod
kubectl -n llm-wiki get pods --show-labels

# Lọc theo label
kubectl -n llm-wiki get pods -l app=frontend
kubectl -n llm-wiki get pods -l 'app in (frontend,backend)'

# Thêm label vào resource
kubectl -n llm-wiki label pod <tên-pod> env=staging

# Xóa label
kubectl -n llm-wiki label pod <tên-pod> env-

# Xem tất cả resource có cùng label (pod, svc, deploy, rs...)
kubectl -n llm-wiki get all -l app=frontend
```

---

## Apply & Delete — triển khai và dọn dẹp

```bash
# Apply tài nguyên từ file YAML (tạo mới hoặc cập nhật)
kubectl apply -f k8s/frontend/deployment.yaml

# Apply toàn bộ thư mục
kubectl apply -f k8s/frontend/

# Apply đệ quy (tất cả thư mục con)
kubectl apply -f k8s/ --recursive

# Dry-run — kiểm tra sẽ thay đổi gì nhưng không apply thật
kubectl apply -f k8s/frontend/ --dry-run=client

# Diff — xem khác biệt giữa file YAML và resource đang chạy
kubectl diff -f k8s/frontend/deployment.yaml

# Xóa resource từ file
kubectl delete -f k8s/frontend/deployment.yaml

# Xóa resource theo tên
kubectl -n llm-wiki delete deployment/frontend
kubectl -n llm-wiki delete pod/<tên-pod>
kubectl -n llm-wiki delete svc/backend
```

---

## JSON / YAML output — lấy dữ liệu dạng máy đọc được

```bash
# Output dạng YAML
kubectl -n llm-wiki get pod <tên-pod> -o yaml

# Output dạng JSON
kubectl -n llm-wiki get deployment/frontend -o json

# Trích xuất trường cụ thể với jsonpath
kubectl -n llm-wiki get pods -o jsonpath='{.items[*].status.podIP}'

# Kết hợp với jq để xử lý JSON
kubectl -n llm-wiki get pods -o json | jq '.items[] | {name: .metadata.name, status: .status.phase}'

# Chỉ lấy danh sách tên pod
kubectl -n llm-wiki get pods -o name
# Output: pod/backend-v2-xxx, pod/frontend-xxx, ...

# In ra image của tất cả container trong deployment
kubectl -n llm-wiki get deploy -o json | jq '.items[] | {name: .metadata.name, images: [.spec.template.spec.containers[].image]}'
```

---

## Bảng cheat sheet tổng hợp (in ra dán tường)

| Việc muốn làm | Lệnh |
|---|---|
| Xem tất cả resource | `kubectl -n llm-wiki get all` |
| Xem pod + IP + node | `kubectl -n llm-wiki get pods -o wide` |
| Xem chi tiết pod | `kubectl -n llm-wiki describe pod <tên>` |
| Xem log pod | `kubectl -n llm-wiki logs <tên>` |
| Follow log | `kubectl -n llm-wiki logs -f <tên>` |
| SSH vào pod | `kubectl -n llm-wiki exec -it <tên> -- bash` |
| Port forward | `kubectl -n llm-wiki port-forward svc/<tên> 8080:8000` |
| Restart deploy | `kubectl -n llm-wiki rollout restart deploy/<tên>` |
| Xem lịch sử rollout | `kubectl -n llm-wiki rollout history deploy/<tên>` |
| Rollback deploy | `kubectl -n llm-wiki rollout undo deploy/<tên>` |
| Scale deploy | `kubectl -n llm-wiki scale deploy/<tên> --replicas=3` |
| Xem events | `kubectl -n llm-wiki get events --sort-by=.metadata.creationTimestamp` |
| Xem tài nguyên pod | `kubectl -n llm-wiki top pods` |
| Xem tài nguyên node | `kubectl top nodes` |
| Apply file YAML | `kubectl apply -f <file.yaml>` |
| Delete resource | `kubectl -n llm-wiki delete <loại>/<tên>` |
| Xem ConfigMap | `kubectl -n llm-wiki get cm` |
| Xem Secret | `kubectl -n llm-wiki get secrets` |
| Giải mã secret | `kubectl -n llm-wiki get secret/<tên> -o jsonpath='{.data.<KEY>}' \| base64 -d` |
