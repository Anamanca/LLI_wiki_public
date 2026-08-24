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
| `healthcheck.sh` | Ops | Healthcheck cluster + app + host (thay monitoring stack đã gỡ) | Kind cluster |
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

### 3. `healthcheck.sh` — liveness & tài nguyên

Thay thế toàn bộ monitoring stack (Prometheus/Grafana/Loki/AlertManager đã gỡ 2026-08-23). Kiểm tra: node cluster, readyReplicas workload, HTTP qua NodePort (`:30080`/`:30081`), worker heartbeat (query Postgres), disk/memory host, CPU kind node. Exit 0 = OK.

```bash
./scripts/healthcheck.sh
# Cron: */10 * * * * /path/scripts/healthcheck.sh >> /tmp/llm-wiki-health.log 2>&1
```

### 4. `dev-local.sh` — local dev

Run backend locally against K8s services (port-forwarded). This script starts uvicorn in the 
background and prints instructions for the frontend.

```bash
./scripts/dev-local.sh
# Then open another terminal for the frontend:
#   cd frontend && npm install && npm run dev
```

### 5. `test-apis.sh` — contract tests

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

### 6. `benchmark_rag.py` — performance evaluation

Measures pipeline latency (p50/p95/p99), throughput, and token usage. Requires direct database 
access (not through the API).

```bash
# Benchmark without tracing (baseline):
LANGSMITH_TRACING=false python scripts/benchmark_rag.py --questions eval/questions.jsonl --output metrics/baseline.json

# Benchmark with tracing (compare overhead):
LANGSMITH_TRACING=true python scripts/benchmark_rag.py --questions eval/questions.jsonl --output metrics/traced.json
```

### 7. `eval_rag.py` — quality evaluation

Evaluates RAG quality against a labeled dataset and optionally pushes results to LangSmith.

```bash
# Dry-run (no LangSmith):
python scripts/eval_rag.py --dataset eval/rag_eval_dataset.jsonl --dry-run

# Full evaluation with LangSmith:
LANGSMITH_TRACING=true python scripts/eval_rag.py --dataset eval/rag_eval_dataset.jsonl --run
```

---

## Common Pitfalls

1. **K3s vs Kind confusion:** `deploy-k8s.sh` = K3s, `sync-and-test.sh` = Kind.
2. **Hardcoded file lists:** `sync-and-test.sh` has a hardcoded list of backend files to sync. When adding new files that need live-reload, update the `files` array.
3. **Benchmark/eval scripts import `llm_wiki` directly:** they need `PYTHONPATH` set and database credentials in `.env`. They bypass the HTTP API.
