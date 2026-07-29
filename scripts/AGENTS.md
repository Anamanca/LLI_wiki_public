# Agent Notes — Scripts

Guidelines for AI agents when using or modifying scripts in this directory.

---

## Script Inventory

| Script | Type | Purpose | Env Req |
|--------|------|---------|---------|
| `dev-local.sh` | Dev | Install deps, port-forward K8s services, start backend | K8s cluster running |
| `test-apis.sh` | Test | Run API contract tests against deployed backend | Backend running |
| `sync-and-test.sh` | Dev | Copy changed source files into K8s pod + run tests | Kind cluster running |
| `deploy-k8s.sh` | Deploy | Build images + deploy to K3s | K3s cluster, Docker |
| `deploy-monitoring.sh` | Deploy | Deploy Prometheus + Grafana + Loki + AlertManager to Kind | Kind/K3s cluster |
| `monitoring-socat-forward.sh` | Dev | Expose all services via socat → Kind node (LAN-accessible) | Kind cluster |
| `port-forward-monitoring.sh` | Dev | Expose monitoring UIs via `kubectl port-forward` (LAN-accessible) | Any K8s cluster |
| `benchmark_rag.py` | Eval | Measure RAG pipeline latency/throughput with tracing on/off | Backend running, DB access |
| `eval_rag.py` | Eval | Evaluate RAG quality against labeled dataset (LangSmith) | Backend running, LangSmith key |

---

## Usage Rules for AI Agents

### 1. `deploy-k8s.sh` — K3s only

This script uses `sudo k3s ctr images import` and is **exclusively for K3s clusters**. Do NOT use it with Kind.

For Kind, use `kind load docker-image` + `kubectl rollout restart` instead (see `k8s/AGENTS.md`).

```bash
# K3s deployment:
./scripts/deploy-k8s.sh

# Kind deployment: follow k8s/AGENTS.md deployment order
```

### 2. `sync-and-test.sh` — Kind only

This script copies files into K8s pods via `kubectl cp` and reloads images with `kind load`. It 
**only works with Kind** (hostPath + hot reload via uvicorn --reload).

The file list in `sync_backend()` is hardcoded — when you add new Python files that should be 
synced for live-reload, add them to the `files` array in `sync_backend()`.

```bash
# Sync backend code to pod (hot reload via uvicorn --reload):
./scripts/sync-and-test.sh --backend

# Rebuild + deploy frontend image:
./scripts/sync-and-test.sh --frontend

# Just run tests against already port-forwarded backend:
./scripts/sync-and-test.sh --test-only

# Full cycle: sync backend → sync frontend → run tests:
./scripts/sync-and-test.sh
```

### 3. `monitoring-socat-forward.sh` — Kind only

Uses `docker inspect` to find the Kind node IP and `socat` to bridge host ports → Kind NodePorts. 
This is specific to Kind's Docker-based architecture.

```bash
# Start all forwarders (app + monitoring):
./scripts/monitoring-socat-forward.sh &

# Port mapping:
#   Frontend:     host:3000  → Kind:30080
#   Backend API:  host:8000  → Kind:30081
#   Grafana:      host:3100  → Kind:30000
#   Prometheus:   host:9090  → Kind:30909
#   AlertManager: host:9093  → Kind:30903
#   Loki:         host:3200  → Kind:30310
```

### 4. `port-forward-monitoring.sh` — any K8s cluster

Uses standard `kubectl port-forward --address 0.0.0.0`. Works with Kind, K3s, or any cluster.

```bash
# Start monitoring port-forwards:
./scripts/port-forward-monitoring.sh &

# Port mapping (binds to 0.0.0.0):
#   Grafana:      host:3000
#   Prometheus:   host:9090
#   AlertManager: host:9093
```

**⚠️ Conflict warning:** Do NOT run both `monitoring-socat-forward.sh` and 
`port-forward-monitoring.sh` simultaneously — they compete for the same host ports.

### 5. `dev-local.sh` — local dev

Run backend locally against K8s services (port-forwarded). This script starts uvicorn in the 
background and prints instructions for the frontend.

```bash
./scripts/dev-local.sh
# Then open another terminal for the frontend:
#   cd frontend && npm install && npm run dev
```

### 6. `test-apis.sh` — contract tests

Runs `tests/test_all_apis.py` against a deployed backend. Auto-detects the URL if not provided.

```bash
# Auto-detect backend:
./scripts/test-apis.sh

# Explicit URL:
./scripts/test-apis.sh http://localhost:8000

# Only frontend-breaking tests:
./scripts/test-apis.sh --critical-only

# Generate JSON report:
./scripts/test-apis.sh --report
```

### 7. `benchmark_rag.py` — performance evaluation

Measures pipeline latency (p50/p95/p99), throughput, and token usage. Requires direct database 
access (not through the API).

```bash
# Benchmark without tracing (baseline):
LANGSMITH_TRACING=false python scripts/benchmark_rag.py --questions eval/questions.jsonl --output metrics/baseline.json

# Benchmark with tracing (compare overhead):
LANGSMITH_TRACING=true python scripts/benchmark_rag.py --questions eval/questions.jsonl --output metrics/traced.json
```

### 8. `eval_rag.py` — quality evaluation

Evaluates RAG quality against a labeled dataset and optionally pushes results to LangSmith.

```bash
# Dry-run (no LangSmith):
python scripts/eval_rag.py --dataset eval/rag_eval_dataset.jsonl --dry-run

# Full evaluation with LangSmith:
LANGSMITH_TRACING=true python scripts/eval_rag.py --dataset eval/rag_eval_dataset.jsonl --run
```

---

## Common Pitfalls

1. **K3s vs Kind confusion:** `deploy-k8s.sh` = K3s, `sync-and-test.sh` = Kind, `port-forward-monitoring.sh` = any cluster.
2. **Port conflicts:** `monitoring-socat-forward.sh` and `port-forward-monitoring.sh` both bind host ports. Only run one at a time.
3. **Hardcoded file lists:** `sync-and-test.sh` has a hardcoded list of backend files to sync. When adding new files that need live-reload, update the `files` array.
4. **socat forward requires Kind container name:** `monitoring-socat-forward.sh` looks for `llm-wiki-control-plane`. If your Kind cluster has a different name, update `KIND_CONTAINER`.
5. **Benchmark/eval scripts import `llm_wiki` directly:** they need `PYTHONPATH` set and database credentials in `.env`. They bypass the HTTP API.
6. **`deploy-monitoring.sh` skips gracefully:** if manifests don't exist, it prints "Skipped" rather than failing. This is intentional — not all monitoring components are always deployed.
