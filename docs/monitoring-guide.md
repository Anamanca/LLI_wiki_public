# LLM Wiki Monitoring Guide

> **Last updated:** 2026-07-29
> **Status:** Deployed & operational
> **Stack:** Prometheus + Grafana + Loki + AlertManager (all in-cluster K8s)

---

## 1. Architecture Overview

The monitoring stack follows the **Google SRE 4-pillar model**:

```
┌──────────────────────────────────────────────────────────────────┐
│                         Grafana (port 3000)                      │
│  ┌──────────────┐  ┌──────────────────┐  ┌───────────────────┐  │
│  │ RED Dashboard│  │ Business (RAG)   │  │ Ingestion Pipeline│  │
│  └──────────────┘  └──────────────────┘  └───────────────────┘  │
└────────────────┬──────────────────┬──────────────────────────────┘
                 │                  │
    ┌────────────▼────────┐  ┌─────▼──────────────────┐
    │     Prometheus      │  │    Loki + Promtail     │
    │   (metrics pull)    │  │  (log aggregation)     │
    │   scrape: 15s       │  │  retention: 7d         │
    └──┬───────────┬──────┘  └────────────────────────┘
       │           │
  ┌────┼────┐ ┌───▼──────────┐
  │Infra   │ │ AlertManager  │
  │Exporters│ │ → null rcv   │  ← replace with Telegram webhook
  │ pg 9187 │ │ (placeholder)│     for production
  │ redis   │ └──────────────┘
  │ 9121    │
  │ minio   │
  │ :9000   │
  └─────────┘
       │
  ┌────▼──────────────────────┐
  │  Application Layer        │
  │  ┌──────────────────────┐ │
  │  │ MetricsPort (ABC)    │ │  ← Clean Architecture port/adapter
  │  │ PrometheusAdapter    │ │
  │  │ GET /api/metrics     │ │  ← FastAPI RED middleware
  │  │ JsonFormatter        │ │  ← trace_id in every log line
  │  └──────────────────────┘ │
  └───────────────────────────┘
```

### 4 Pillars

| Pillar | Tool | What It Tells You |
|--------|------|-------------------|
| **Metrics** | Prometheus + Grafana | RED metrics, business KPIs, infra health — "is the system working?" |
| **Logging** | Loki + Promtail | Structured JSON logs, trace_id correlation — "what happened during this request?" |
| **Tracing** | LangSmith (existing) | Full parent-child span tree per query — "which step was slow?" |
| **Alerting** | AlertManager | 8 PromQL alert rules → Telegram — "someone needs to know NOW" |

### Clean Architecture Pattern

Application metrics follow the same port/adapter pattern as the rest of the codebase:

```
application/ports/telemetry/metrics_port.py    ← ABC: MetricsPort
infrastructure/telemetry/prometheus_metrics_adapter.py  ← Concrete adapter
infrastructure/telemetry/null_metrics_adapter.py        ← No-op fallback
infrastructure/telemetry/metrics_collector.py           ← Singleton factory
infrastructure/telemetry/business_metrics.py            ← Helper functions
presentation/middleware/metrics_middleware.py            ← RED middleware
presentation/routes/metrics.py                          ← /api/metrics endpoint
infrastructure/telemetry/logging_config.py              ← JSON + trace_id
```

---

## 2. Quick Start

### Prerequisites

- Running Kubernetes cluster (Kind/K3s)
- `kubectl` configured with cluster access
- `llm-wiki` namespace created
- Docker image already built and loaded (for worker sidecars)

### One-command deploy

```bash
./scripts/deploy-monitoring.sh
```

This deploys Prometheus, Grafana, Loki, Promtail, AlertManager, and alert rules — all configured, no manual steps.

### What gets deployed

| Component | Manifest(s) | K8s Kind | Port |
|-----------|------------|----------|------|
| Prometheus | `prometheus-rbac.yaml`, `prometheus-config.yaml`, `prometheus-deployment.yaml` | Deployment + ConfigMap + ClusterRole | 9090 |
| Grafana | `grafana-datasources.yaml`, `grafana-dashboards.yaml`, `grafana-deployment.yaml` | Deployment + ConfigMap | 3000 |
| Loki | `loki-statefulset.yaml` (includes ConfigMap inline) | StatefulSet + Service | 3100 |
| Promtail | `promtail-daemonset.yaml` (includes ConfigMap inline) | DaemonSet + ConfigMap | 9080 |
| AlertManager | `alertmanager-config.yaml`, `alertmanager-deployment.yaml` | Deployment + ConfigMap | 9093 |
| Alert Rules | `prometheus-alert-rules.yaml` | ConfigMap (mounted into Prometheus) | — |

### Enabling metrics on app pods

Set in `k8s/configmap.yaml`:
```yaml
ENABLE_METRICS: "true"
LOG_FORMAT: "json"
```

All three entrypoints (backend, cpu-worker, wiki-consumer) read these via `settings.enable_metrics` / `settings.log_format`. The feature gate means metrics ports are only bound when explicitly enabled.

### Access the dashboards

```bash
# Start all port-forwards in background
kubectl -n llm-wiki port-forward svc/grafana 3000:3000 &
kubectl -n llm-wiki port-forward svc/prometheus 9090:9090 &
kubectl -n llm-wiki port-forward svc/alertmanager 9093:9093 &

# Or use the convenience script
# ./scripts/port-forward-monitoring.sh
```

| Service | URL | Default Credentials |
|---------|-----|---------------------|
| Grafana | http://localhost:3000 | `admin` / `admin` |
| Prometheus | http://localhost:9090 | None (no auth) |
| AlertManager | http://localhost:9093 | None (no auth) |

---

## 3. Dashboard Reference

### Dashboard 1: RED — HTTP Overview

**UID:** `llm-wiki-red`

The standard Google SRE RED dashboard (Rate, Errors, Duration).

| Panel | PromQL | What It Shows |
|-------|--------|---------------|
| **Request Rate** | `sum(rate(http_requests_total[5m]))` | Overall RPS across all endpoints |
| **Error Rate (5xx)** | `sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` | 5xx error ratio — alert fires at > 5% |
| **P95 Latency** | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, path))` | Per-endpoint P95 latency breakdown |
| **Request Rate by Endpoint** | `sum(rate(http_requests_total[5m])) by (path, method)` | Traffic distribution across API endpoints |
| **Status Code Distribution** | `sum(rate(http_requests_total[5m])) by (status)` | Pie chart of 2xx/3xx/4xx/5xx split |

**Interview talking point:** This dashboard proves we instrument every HTTP request with path normalization (UUID→`:uuid`, numbers→`:num`) to prevent label cardinality explosion.

---

### Dashboard 2: Business — RAG Pipeline

**UID:** `llm-wiki-business`

Shows the business value of monitoring: cache efficiency, LLM costs, query performance.

| Panel | PromQL | What It Shows |
|-------|--------|---------------|
| **Cache Hit Rate** | `sum(rate(cache_hit_total[5m])) / (sum(rate(cache_hit_total[5m])) + sum(rate(cache_miss_total[5m])))` | 3-tier cache effectiveness (exact → semantic → LLM) |
| **LLM Tokens / Hour** | `sum(increase(llm_tokens_used_total[1h])) by (model)` | Token consumption by model — cost tracking |
| **Query Count / min** | `sum(rate(query_total{status="success"}[1m]))` | Successful queries per minute |
| **Avg Synthesis Latency** | `rate(llm_synthesis_duration_seconds_sum[5m]) / rate(llm_synthesis_duration_seconds_count[5m])` | Average LLM answer generation time |
| **Pipeline Stage Durations (P95)** | `histogram_quantile(0.95, ...)` on embedding, vector search, keyword search, LLM synthesis | Bottleneck detection across RAG stages |

**Interview talking point:** This dashboard directly connects to business value — cache hit rate → lower LLM costs, token tracking → budget control, stage breakdown → optimization targets.

---

### Dashboard 3: Ingestion — Pipeline

**UID:** `llm-wiki-ingestion`

Tracks the YouTube transcript → wiki integration pipeline.

| Panel | PromQL | What It Shows |
|-------|--------|---------------|
| **Jobs Completed / Failed** | `sum(rate(ingestion_jobs_total{status="completed"}[1h])) by (worker_id)` vs `{status="failed"}` | Worker throughput and success rate |
| **Queue Depth** | `ingestion_queue_depth` | Pending items per queue (cpu/wiki) |
| **Job Duration (p95)** | `histogram_quantile(0.95, sum(rate(ingestion_job_duration_seconds_bucket[5m])) by (le, stage))` | Per-stage processing time |
| **Worker Status** | `worker_heartbeat_age_seconds` | How recently each worker emitted a heartbeat |
| **Worker CPU %** | `worker_cpu_percent` | CPU utilization per worker |

**Interview talking point:** Worker heartbeats + queue depth = proving your pipeline has back-pressure awareness. The 3-pass wiki integrator stages are visible.

---

## 4. Metrics Reference

### Application Metrics (RED)

| Metric Name | Type | Labels | Source |
|-------------|------|--------|--------|
| `http_requests_total` | Counter | `method`, `path`, `status` | `MetricsMiddleware` (every HTTP request) |
| `http_request_duration_seconds` | Histogram | `method`, `path` | `MetricsMiddleware` (every HTTP request) |

Path normalization rules:
- `/api/sources/550e8400-e29b-41d4-a716-446655440000` → `/api/sources/:uuid`
- `/api/pages/507f1f77bcf86cd799439011` → `/api/pages/:id`
- `/api/sources/42` → `/api/sources/:num`

### Business Metrics (RAG Pipeline)

These are emitted via the `business_metrics.py` helpers (`inc_counter`, `track_duration`, `set_gauge`).

| Metric Name | Type | Labels | Emitted By |
|-------------|------|--------|------------|
| `cache_hit_total` | Counter | `cache_level` (`exact`/`semantic`/`llm`) | Cache layer |
| `cache_miss_total` | Counter | `cache_level` | Cache layer |
| `query_total` | Counter | `status` (`success`/`error`) | Query service |
| `llm_tokens_used_total` | Counter | `model`, `direction` (`input`/`output`) | LLM client |
| `llm_synthesis_duration_seconds` | Histogram | — | LLM synthesis step |
| `embedding_duration_seconds` | Histogram | — | Embedding generation |
| `vector_search_duration_seconds` | Histogram | — | pgvector similarity search |
| `keyword_search_duration_seconds` | Histogram | — | PostgreSQL full-text search |

### Worker Metrics

| Metric Name | Type | Labels | Source |
|-------------|------|--------|--------|
| `ingestion_jobs_total` | Counter | `worker_id`, `status` (`completed`/`failed`), `stage` | Worker pipeline |
| `ingestion_job_duration_seconds` | Histogram | `stage` | Worker pipeline |
| `ingestion_queue_depth` | Gauge | `queue` (`cpu`/`wiki`) | Workers refresh on heartbeat |
| `worker_heartbeat_age_seconds` | Gauge | `worker_id` | Updated every worker heartbeat cycle |
| `worker_cpu_percent` | Gauge | `worker_id` | CPU worker's own `psutil` measurement |

### Infrastructure Metrics

| Metric Name | Source | Key Indicators |
|-------------|--------|---------------|
| `pg_stat_database_tup_fetched` | `postgres-exporter:9187` | Database read throughput |
| `pg_stat_activity_count` | `postgres-exporter:9187` | Active connections |
| `pg_database_size_bytes` | `postgres-exporter:9187` | Disk usage per database |
| `pg_locks_count` | `postgres-exporter:9187` | Lock contention |
| `redis_connected_clients` | `redis-exporter:9121` | Active Redis connections |
| `redis_used_memory_bytes` | `redis-exporter:9121` | Memory usage |
| `minio_cluster_bucket_total` | MinIO built-in metrics `:9000` | Bucket count |
| `minio_cluster_capacity_raw_free_bytes` | MinIO `:9000` | Free storage |

### How to add a new metric

```python
from llm_wiki.infrastructure.telemetry.business_metrics import inc_counter, track_duration, set_gauge

# Counter — increment by 1
inc_counter("my_event_total", {"status": "success"})

# Histogram — measure duration of a block
with track_duration("my_operation_duration_seconds", {"phase": "extract"}):
    do_expensive_work()

# Gauge — set point-in-time value
set_gauge("my_capacity_remaining", 42.0, {"pool": "embeddings"})
```

No imports from `prometheus_client`. No conditional checks. When `ENABLE_METRICS=false`, all three functions are no-ops. This is the Clean Architecture pattern — call sites don't know which adapter is wired.

---

## 5. Alert Reference

All 8 alert rules are defined in `k8s/monitoring/prometheus-alert-rules.yaml`.

### Application Alerts

| Alert | Severity | Trigger | For | Runbook |
|-------|----------|---------|-----|---------|
| **HighErrorRate** | 🔴 critical | 5xx rate > 5% over 5 min | 5m | `kubectl logs -n llm-wiki deploy/backend-v2 --tail=100` |
| **QueryLatencyHigh** | 🟡 warning | P95 query latency > 30s | 15m | Check LLM provider status and Ollama embedding latency |
| **WorkerStalled** | 🔴 critical | Worker heartbeat age > 120s | 2m | `kubectl logs -n llm-wiki deploy/cpu-worker -c cpu-worker` |
| **QueueBacklogHigh** | 🟡 warning | CPU queue depth > 100 | 10m | Check worker capacity; may need to scale replicas |
| **LLMApiErrorRate** | 🔴 critical | LLM API error rate > 5% | 5m | Check API key quota and provider status page |

### Infrastructure Alerts

| Alert | Severity | Trigger | For | Runbook |
|-------|----------|---------|-----|---------|
| **PostgresDown** | 🔴 critical | `up{job="postgres"} == 0` | 1m | Check postgres pod: `kubectl get pods -n llm-wiki -l app=postgres` |
| **RedisDown** | 🟡 warning | `up{job="redis"} == 0` | 1m | Cache degraded but app still functional on DB fallback |
| **DiskSpaceLow** | 🔴 critical | Root filesystem < 10% free | 5m | Clean old logs, containers, or expand disk |

### Viewing alerts

```bash
# List all alert rules and their state
kubectl -n llm-wiki exec deploy/prometheus -- wget -qO- http://localhost:9090/api/v1/rules

# Query which alerts are currently firing
kubectl -n llm-wiki exec deploy/prometheus -- wget -qO- http://localhost:9090/api/v1/alerts
```

### Telegram Integration (production upgrade)

The current AlertManager config uses a `null` receiver (dummy placeholder). To enable Telegram:

1. Add `TELEGRAM_ALERT_CHAT_ID` to `k8s/secret.yaml` (do NOT commit — it's in `.gitignore`-protected secrets):
   ```yaml
   TELEGRAM_ALERT_CHAT_ID: "-1001234567890"  # your Telegram group chat ID
   ```

2. Get a Telegram bot token from [@BotFather](https://t.me/BotFather).

3. Update `k8s/monitoring/alertmanager-config.yaml`:
   ```yaml
   receivers:
   - name: 'telegram'
     telegram_configs:
     - bot_token: '<BOT_TOKEN>'
       chat_id: <CHAT_ID>       # integer, no quotes
       parse_mode: 'HTML'
       message: |
         🚨 <b>{{ .GroupLabels.alertname }}</b>
         Severity: {{ .CommonLabels.severity }}
         {{ range .Alerts }}
         • {{ .Annotations.description }}
         Runbook: {{ .Annotations.runbook }}
         {{ end }}
   ```

4. Update the route to use `receiver: 'telegram'`.

5. Re-apply and restart AlertManager:
   ```bash
   kubectl apply -f k8s/monitoring/alertmanager-config.yaml
   kubectl -n llm-wiki rollout restart deploy/alertmanager
   ```

---

## 6. Logging with trace_id Correlation

### Architecture

Every entrypoint calls `setup_logging()` once at startup:

```python
# In main.py (backend) or entrypoint (cpu_worker, wiki_consumer)
from llm_wiki.infrastructure.telemetry.logging_config import setup_logging
setup_logging(service_name="backend-v2", log_format=settings.log_format)
```

This installs:
- **`JsonFormatter`** — emits `{"timestamp": "...", "level": "INFO", "service": "backend-v2", "trace_id": "...", "message": "..."}` when `LOG_FORMAT=json`
- **`TraceIdFilter`** — reads the LangSmith `trace_id` from a `contextvars.ContextVar` and injects it into every log record — async-safe across asyncio tasks

### How trace_id propagates

```
LangSmith RunTree.start_span()
  └─ _current_trace_id.set(str(run.id))    ← contextvars, async-safe
         │
         ▼
  Any logging.getLogger().info("processing...")
         │
         ▼
  TraceIdFilter.filter()
    └─ get_current_trace_id() → injects "trace_id" field
         │
         ▼
  JsonFormatter formats as JSON line
         │
         ▼
  stdout → Promtail → Loki
```

### Querying logs in Loki via Grafana

1. Open any dashboard in Grafana
2. Click **Explore** → select **Loki** datasource
3. Query examples:

```logql
# All logs from a specific service
{service_name="backend-v2"}

# Logs correlated with a specific trace (copy trace_id from LangSmith UI)
{service_name="backend-v2"} |= "4d0fc7a4-"

# Error-level logs
{namespace="llm-wiki"} |= "level\": \"ERROR"

# Logs from a specific pod
{pod="backend-v2-85f4b686c5-5dtv4"}
```

**Interview talking point:** `trace_id` correlation means you can go from a LangSmith trace ("this query was slow") → copy the trace ID → query Loki for all related logs → see exactly which step failed and why. This is the observability trinity: metrics tell you there's a problem, traces show you where, logs tell you why.

---

## 7. How Metrics Flow — End to End

### RED Metrics (Backend API)

```
1. Client sends GET /api/query?question=...
2. FastAPI → MetricsMiddleware.dispatch()
3. Normalize path: /api/query → /api/query (no params in path, kept as-is)
4. Record start time
5. Execute downstream handler
6. Record elapsed time
7. Emit:
   - http_requests_total{method="GET", path="/api/query", status="200"} += 1
   - http_request_duration_seconds{method="GET", path="/api/query"}.observe(elapsed)
8. Prometheus scrapes GET /api/metrics every 15s
9. Grafana panel queries Prometheus every 30s
```

### Worker Metrics (Health Server)

```
1. cpu_worker starts → asyncio.start_server(handle_health, "0.0.0.0", 8101)
2. Worker heartbeat loop emits:
   - worker_heartbeat_age_seconds{worker_id="1"} = 0
   - worker_cpu_percent{worker_id="1"} = 42.5
   - ingestion_queue_depth{queue="cpu"} = 15
3. Prometheus scrapes GET /metrics on :8101 every 15s
4. WorkerStalled alert evaluates: (time() - timestamp(worker_heartbeat_age_seconds)) > 120
```

### Infrastructure Metrics

```
1. postgres-exporter sidecar queries pg_stat_* views via DATA_SOURCE_URI
2. redis-exporter sidecar calls INFO command via redis.addr=localhost:6379
3. MinIO native /minio/v2/metrics/cluster (MINIO_PROMETHEUS_AUTH_TYPE=public)
4. Prometheus scrapes all three via static_configs / kubernetes_sd_configs
5. PostgresDown / RedisDown alerts evaluate up{} == 0
```

---

## 8. K8s Manifest Map

```
k8s/
├── configmap.yaml                  ← ENABLE_METRICS: "true", LOG_FORMAT: "json"
├── backend/deployment.yaml         ← prometheus.io annotations
├── cpu-worker/deployment.yaml      ← prometheus.io annotations + health_server port 8101
├── wiki-consumer/statefulset.yaml  ← prometheus.io annotations + health_server port 8201
├── postgres/
│   ├── statefulset.yaml            ← postgres-exporter sidecar :9187
│   └── service.yaml                ← port 9187 exposed
├── redis/
│   ├── deployment.yaml             ← redis-exporter sidecar :9121
│   └── service.yaml                ← port 9121 exposed
├── minio/
│   └── statefulset.yaml            ← prometheus.io scrape annotation + auth=public
└── monitoring/
    ├── prometheus-rbac.yaml         ← ServiceAccount + ClusterRole + Binding
    ├── prometheus-config.yaml       ← scrape_configs (pod SD + static)
    ├── prometheus-deployment.yaml   ← Deployment + emptyDir
    ├── prometheus-alert-rules.yaml  ← 8 alert rules
    ├── grafana-datasources.yaml     ← Prometheus + Loki datasources
    ├── grafana-dashboards.yaml      ← 3 dashboard JSONs
    ├── grafana-deployment.yaml      ← Deployment + emptyDir
    ├── alertmanager-config.yaml     ← null receiver (placeholder)
    ├── alertmanager-deployment.yaml ← Deployment + emptyDir
    ├── loki-statefulset.yaml        ← StatefulSet + ConfigMap (7d retention)
    └── promtail-daemonset.yaml      ← DaemonSet + ConfigMap (cri stage)
```

---

## 9. Prometheus Target Health

All 7 scrape targets (verified 2026-07-23):

| Status | Job | Endpoint |
|--------|-----|----------|
| 🟢 UP | `backend-v2` | Backend API `:8000/api/metrics` |
| 🟢 UP | `backend-v2` | CPU Worker `:8101/metrics` |
| 🟢 UP | `backend-v2` | Wiki Consumer 0 `:8201/metrics` |
| 🟢 UP | `backend-v2` | Wiki Consumer 1 `:8201/metrics` |
| 🟢 UP | `backend-v2` | MinIO `:9000/minio/v2/metrics/cluster` |
| 🟢 UP | `postgres` | postgres-exporter `:9187/metrics` |
| 🟢 UP | `redis` | redis-exporter `:9121/metrics` |

Check at any time:
```bash
kubectl -n llm-wiki exec deploy/prometheus -- wget -qO- http://localhost:9090/api/v1/targets
```

---

## 10. Interview Talking Points

When demoing this to a senior developer interviewer, highlight these aspects:

### Architecture Discipline
> "All application metrics follow Clean Architecture — `MetricsPort` is an ABC in the application layer. The concrete `PrometheusMetricsAdapter` implements it in infrastructure. Call sites use `inc_counter()` / `track_duration()`, which are zero-dependency helpers. When Prometheus isn't enabled, a `NullMetricsAdapter` takes over — zero performance cost, zero code branches at call sites."

### SRE 4-Pillar Model
> "I implemented all four pillars of the Google SRE observability model: Metrics (Prometheus + Grafana), Logging (structured JSON → Loki), Tracing (LangSmith with trace_id injected into logs), and Alerting (8 PromQL rules → AlertManager). The trace_id bridges logs and traces — you can copy a trace ID from LangSmith and grep Loki for the exact log lines of that request."

### RED + Business Dashboards
> "The RED dashboard proves every HTTP request is instrumented with path normalization to prevent cardinality explosion. The Business dashboard proves monitoring isn't just ops overhead — cache hit rate directly maps to LLM cost savings, token tracking maps to budget control."

### Infrastructure as Code
> "The entire monitoring stack — Prometheus, Grafana, Loki, Promtail, AlertManager, plus all dashboards, alert rules, RBAC, and scrape configs — deploys with one shell script. Every component is a K8s manifest in `k8s/monitoring/`. Zero cloud dependencies — everything runs in-cluster."

### Practical Incident Response
> "WorkerStalled fires within 2 minutes of a missing heartbeat. The runbook in the alert annotation tells the on-call exactly which command to run. That's the difference between 'something is wrong' and 'here's what to do about it.'"

---

## 11. Troubleshooting

### Prometheus target is DOWN

```bash
# Check target details
kubectl -n llm-wiki exec deploy/prometheus -- wget -qO- http://localhost:9090/api/v1/targets | python3 -m json.tool

# Verify the target pod is running and has annotations
kubectl -n llm-wiki get pod <pod-name> -o yaml | grep -A3 prometheus.io

# Check metrics endpoint directly
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://<target-ip>:<port>/metrics
```

### Grafana dashboard shows "No data"

1. Check the Prometheus datasource is healthy: Grafana → Connections → Data Sources → Prometheus → Save & Test
2. Verify PromQL query works in Prometheus directly: http://localhost:9090 → Graph → paste the query
3. Check time range — dashboards default to "Last 1 hour"; expand to "Last 6 hours" or "Last 24 hours"

### Loki shows no log streams

```bash
# Check Promtail targets
kubectl -n llm-wiki logs -l app=promtail --tail=50 | grep -i error

# Check Loki is ready
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://loki.llm-wiki.svc.cluster.local:3100/ready

# Check available labels
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://loki.llm-wiki.svc.cluster.local:3100/loki/api/v1/label

# Verify Promtail positions are advancing
kubectl -n llm-wiki exec -l app=promtail -- cat /tmp/positions.yaml | wc -l
```

If positions are empty, the Promtail config likely has a `__path__` mismatch. Kind uses containerd (CRI format), not Docker. Verify `pipeline_stages` has `cri: {}` not `docker: {}`.

### Worker metrics return empty

The worker health server returns Prometheus metrics. If workers return empty (`Content-Length: 0`):

1. Check `ENABLE_METRICS=true` is set in the pod:
   ```bash
   kubectl -n llm-wiki exec deploy/cpu-worker -c cpu-worker -- python3 -c "import os; print(os.environ.get('ENABLE_METRICS'))"
   ```

2. If the pod runs an old Docker image, rebuild and reload:
   ```bash
   docker build --network=host -t 32_llm_wiki_clean_arch-backend:latest .
   kind load docker-image 32_llm_wiki_clean_arch-backend:latest --name llm-wiki
   kubectl -n llm-wiki rollout restart deploy/cpu-worker
   kubectl -n llm-wiki rollout restart statefulset/wiki-consumer
   ```

### Postgres/Redis exporter crashes

```bash
# Check exporter logs
kubectl -n llm-wiki logs postgres-0 -c postgres-exporter --tail=20
kubectl -n llm-wiki logs deploy/redis -c redis-exporter --tail=20

# Common causes:
# - DATA_SOURCE_URI wrong format (must be localhost:5432/db?sslmode=disable&user=...)
# - DATA_SOURCE_PASS secret key not present
# - redis-exporter can't connect to localhost:6379
```

### AlertManager not sending to Telegram

The current setup uses a `null` receiver (controlled placeholder for demo). To verify AlertManager is operational:

```bash
# Check AlertManager health
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://alertmanager.llm-wiki.svc.cluster.local:9093/-/healthy

# Post a test alert manually
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s -X POST \
  http://alertmanager.llm-wiki.svc.cluster.local:9093/api/v2/alerts \
  -H 'Content-Type: application/json' \
  -d '[{"labels":{"alertname":"TestAlert","severity":"warning"},"annotations":{"summary":"Manual test"}}]'
```

For Telegram integration, follow the instructions in Section 5.

---

## 12. File Reference — Monitoring Code

| File | Purpose |
|------|---------|
| `src/llm_wiki/application/ports/telemetry/metrics_port.py` | ABC: `MetricsPort` with `counter()`, `histogram()`, `gauge()`, `get_metrics_response()` |
| `src/llm_wiki/infrastructure/telemetry/prometheus_metrics_adapter.py` | Concrete adapter: lazy Counter/Histogram/Gauge creation, `generate_latest()` |
| `src/llm_wiki/infrastructure/telemetry/null_metrics_adapter.py` | No-op adapter: all methods return without side effects |
| `src/llm_wiki/infrastructure/telemetry/metrics_collector.py` | Singleton factory `get_metrics()` — returns Prometheus or Null adapter |
| `src/llm_wiki/infrastructure/telemetry/business_metrics.py` | `inc_counter()`, `track_duration()`, `set_gauge()` helpers |
| `src/llm_wiki/presentation/middleware/metrics_middleware.py` | FastAPI RED middleware with path normalization |
| `src/llm_wiki/presentation/routes/metrics.py` | `GET /api/metrics` → Prometheus text format |
| `src/llm_wiki/infrastructure/telemetry/logging_config.py` | `JsonFormatter`, `TraceIdFilter`, `setup_logging()` |
| `src/llm_wiki/infrastructure/telemetry/langsmith_telemetry_adapter.py` | `_current_trace_id` ContextVar for log correlation |
| `src/llm_wiki/infrastructure/entrypoints/health_server.py` | Worker `/metrics` endpoint (when `ENABLE_METRICS=true`) |
| `src/llm_wiki/main.py` | Wires `MetricsMiddleware` + `/api/metrics` route when enabled |
| `src/llm_wiki/config.py` | `enable_metrics: bool`, `log_format: str` |
| `scripts/deploy-monitoring.sh` | One-command monitoring stack deployment |
