# LLM Wiki — Observability Guide

> **Last updated:** 2026-07-30
> **Stack:** Prometheus · Grafana · Loki · Promtail · LangSmith · AlertManager
> **Model:** Google SRE 4-pillar (Metrics, Logging, Tracing, Alerting)
> **Deployment:** Fully in-cluster K8s (`llm-wiki` namespace), zero cloud dependencies

---

## Table of Contents

1. [Architecture](#1-architecture--data-flow)
2. [Quick Start](#2-quick-start)
3. [Pillar 1 — Metrics (Prometheus + Grafana)](#3-pillar-1--metrics)
4. [Pillar 2 — Logging (Loki + Promtail)](#4-pillar-2--logging)
5. [Pillar 3 — Tracing (LangSmith)](#5-pillar-3--tracing)
6. [Pillar 4 — Alerting (AlertManager + Loki Ruler)](#6-pillar-4--alerting)
7. [Incident Response](#7-incident-response)
8. [Troubleshooting](#8-troubleshooting)
9. [K8s Manifest Map](#9-k8s-manifest-map)
10. [Code Reference](#10-code-reference)

---

## 1. Architecture & Data Flow

### 1.1 The big picture

```
                         ┌─────────────────────────────────────────────┐
                         │              Grafana (port 3000)             │
                         │  ┌──────────┐ ┌──────────┐ ┌────────────┐  │
                         │  │   RED    │ │ Business │ │ Ingestion  │  │
                         │  │   HTTP   │ │   RAG    │ │  Pipeline  │  │
                         │  └────┬─────┘ └────┬─────┘ └─────┬──────┘  │
                         │       │            │             │          │
                         │  ┌────┴────────────┴─────────────┴──────┐   │
                         │  │        Loki panels (5 built-in)      │   │
                         │  └──────────────────────────────────────┘   │
                         └───────┬──────────────────┬──────────────────┘
                                 │                  │
              ┌──────────────────▼─────┐  ┌─────────▼──────────────────┐
              │      Prometheus        │  │      Loki (StatefulSet)    │
              │   scrape interval: 15s │  │   retention: 7d, PVC 1Gi  │
              │   tsdb: emptyDir       │  │   ruler: 3 LogQL alerts   │
              └──┬──────────┬──────────┘  └────────────┬───────────────┘
                 │          │                          │
    ┌────────────┼──────┐   │               ┌──────────▼──────────┐
    │ Application Layer │   │               │  Promtail DaemonSet │
    │  ┌───────────────┐│   │               │  cri + json stages  │
    │  │ MetricsPort   ││   │               │  /var/log/pods/...  │
    │  │ Prometheus    ││   │               └─────────────────────┘
    │  │ Adapter       ││   │
    │  │ /api/metrics  ││   │
    │  └───────────────┘│   │
    │  ┌───────────────┐│   │
    │  │ JsonFormatter ││   │
    │  │ TraceIdFilter ││   │
    │  └───────────────┘│   │
    └───────────────────┘   │
                            ▼
              ┌─────────────────────────┐
              │     AlertManager        │
              │  8 PromQL + 3 LogQL     │
              │  → null receivers       │  ← wire Telegram/Slack for prod
              └─────────────────────────┘
```

### 1.2 How the 4 pillars connect

| Pillar | Tool | Question Answered | Data Source |
|--------|------|------------------|-------------|
| **Metrics** | Prometheus + Grafana | "Is the system healthy?" | App counters/histograms/gauges via `MetricsPort` |
| **Logging** | Loki + Promtail | "What exactly happened?" | stdout → Promtail → Loki JSON-parsed |
| **Tracing** | LangSmith | "Which step was slow/failed?" | `RunTree` spans via `TelemetryPort` |
| **Alerting** | AlertManager + Loki Ruler | "Who needs to know right now?" | 11 alert rules evaluated in-cluster |

### 1.3 Cross-pillar correlation (the observability trinity)

The **critical link** is `trace_id` — it bridges LangSmith spans and Loki log lines:

```
                      ┌─────────────────────┐
                      │     LangSmith UI     │
                      │ "query took 12.4s"   │
                      │  Trace ID: 4d0fc7a4  │
                      └──────────┬───────────┘
                                 │ copy trace_id
                                 ▼
                      ┌─────────────────────┐
                      │    Grafana Explore   │
                      │   {service="backend- │
                      │    v2"} |= "4d0fc7a4"│
                      └──────────┬───────────┘
                                 │
                                 ▼
              ┌─────────────────────────────────────┐
              │ {"timestamp":"...","level":"ERROR",  │
              │  "message":"OpenAI API timeout on    │
              │   embeddings","trace_id":"4d0fc7a4"} │
              └─────────────────────────────────────┘
```

**Workflow**: LangSmith shows you a slow trace → copy the trace ID → query Loki → see every log line for that exact request.

---

## 2. Quick Start

### 2.1 Deploy the monitoring stack

```bash
# One command — deploys Prometheus, Grafana, Loki, Promtail, AlertManager, all dashboards and alerts
./scripts/deploy-monitoring.sh
```

### 2.2 Access the tools

```bash
# Port-forward all monitoring services
kubectl -n llm-wiki port-forward svc/grafana 3000:3000 &
kubectl -n llm-wiki port-forward svc/prometheus 9090:9090 &
kubectl -n llm-wiki port-forward svc/alertmanager 9093:9093 &

# Or use the convenience script for LAN access
./scripts/port-forward-monitoring.sh
```

| Service | URL | Auth |
|---------|-----|------|
| Grafana | http://localhost:3000 | `admin` / `admin` |
| Prometheus | http://localhost:9090 | None |
| AlertManager | http://localhost:9093 | None |
| LangSmith | https://smith.langchain.com | API key → project `llm-wiki-rag` |

### 2.3 Enable metrics and structured logging

In `k8s/configmap.yaml`:
```yaml
ENABLE_METRICS: "true"   # Bind /metrics endpoints on all services
LOG_FORMAT: "json"        # Structured JSON → Loki
LOG_LEVEL: "INFO"         # DEBUG | INFO | WARNING | ERROR | CRITICAL
```

When `ENABLE_METRICS=false`, the `NullMetricsAdapter` takes over — zero overhead, no conditional branches in business logic. Same pattern for LangSmith: when `LANGSMITH_TRACING=false`, the `NullTelemetryAdapter` silently drops all spans.

---

## 3. Pillar 1 — Metrics

### 3.1 How metrics flow

```
Application code                          Prometheus                  Grafana
───────────────                          ──────────                  ───────
inc_counter("query_total",               scrapes /api/metrics        queries every 30s
  {status:"success"})         ──→        every 15s         ──→      renders panel
```

### 3.2 Port/Adapter pattern

All application metrics go through the `MetricsPort` ABC — business logic never imports `prometheus_client`:

```
application/ports/telemetry/metrics_port.py       ← ABC
infrastructure/telemetry/prometheus_metrics_adapter.py  ← Real implementation
infrastructure/telemetry/null_metrics_adapter.py        ← No-op (when disabled)
infrastructure/telemetry/metrics_collector.py           ← Singleton factory
infrastructure/telemetry/business_metrics.py            ← Convenience helpers
```

### 3.3 Convenience helpers (use these, not raw Prometheus)

```python
from llm_wiki.infrastructure.telemetry.business_metrics import (
    inc_counter,    # increment a Counter
    track_duration, # context manager for Histogram
    set_gauge,      # set a Gauge
)

# Counter — fire-and-forget
inc_counter("cache_hit_total", {"cache_level": "exact"})

# Histogram — measure a code block
with track_duration("embedding_duration_seconds", {"model": "bge-m3"}):
    result = await embed(text)

# Gauge — point-in-time snapshot
set_gauge("ingestion_queue_depth", pending_count, {"queue": "cpu"})
```

When `ENABLE_METRICS=false`, all three are **no-ops** — no conditional branches at call sites, no performance penalty.

### 3.4 Metric catalog

#### RED Metrics (every HTTP request — `MetricsMiddleware`)

| Metric | Type | Labels |
|--------|------|--------|
| `http_requests_total` | Counter | `method`, `path`, `status` |
| `http_request_duration_seconds` | Histogram | `method`, `path` |

Path normalization prevents label cardinality explosion:
- `/api/sources/550e8400-e29b-41d4-a716-446655440000` → `/api/sources/:uuid`
- `/api/sources/42` → `/api/sources/:num`
- `/api/pages/507f1f77bcf86cd799439011` → `/api/pages/:id`

#### Business Metrics (RAG pipeline)

| Metric | Type | Emitted By |
|--------|------|------------|
| `cache_hit_total` | Counter (`cache_level`) | Cache layer |
| `cache_miss_total` | Counter (`cache_level`) | Cache layer |
| `query_total` | Counter (`status`) | Query service |
| `llm_tokens_used_total` | Counter (`model`, `direction`) | LLM client |
| `llm_synthesis_duration_seconds` | Histogram | LLM synthesis |
| `embedding_duration_seconds` | Histogram | Embedding generation |
| `vector_search_duration_seconds` | Histogram | pgvector similarity search |
| `keyword_search_duration_seconds` | Histogram | PostgreSQL full-text search |

#### Worker Metrics

| Metric | Type | Source |
|--------|------|--------|
| `ingestion_jobs_total` | Counter (`worker_id`, `status`, `stage`) | Worker pipeline |
| `ingestion_job_duration_seconds` | Histogram (`stage`) | Worker pipeline |
| `ingestion_queue_depth` | Gauge (`queue`) | Worker heartbeat loop |
| `worker_heartbeat_age_seconds` | Gauge (`worker_id`) | Worker heartbeat loop |
| `worker_cpu_percent` | Gauge (`worker_id`) | CPU worker `psutil` |

#### Infrastructure Metrics (via exporters)

| Exporter | Port | Key Metrics |
|----------|------|-------------|
| postgres-exporter (sidecar) | 9187 | `pg_stat_database_tup_fetched`, `pg_stat_activity_count`, `pg_database_size_bytes`, `pg_locks_count` |
| redis-exporter (sidecar) | 9121 | `redis_connected_clients`, `redis_used_memory_bytes` |
| MinIO (built-in) | 9000 | `minio_cluster_bucket_total`, `minio_cluster_capacity_raw_free_bytes` |
| node-exporter (DaemonSet) | 9100 | Host CPU, memory, disk, network |

### 3.5 Grafana dashboards

All 3 dashboards are provisioned automatically via ConfigMap — no manual setup.

#### Dashboard 1: RED — HTTP Overview (`uid: llm-wiki-red`)

| Panel | Query | What to watch |
|-------|-------|---------------|
| Request Rate | `sum(rate(http_requests_total[5m]))` | Overall traffic |
| Error Rate (5xx) | `sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` | Alert fires at >5% |
| P95 Latency | `histogram_quantile(0.95, ...)` by path | Per-endpoint performance |
| Request Rate by Endpoint | `sum(rate(http_requests_total[5m])) by (path, method)` | Traffic distribution |
| Status Code Distribution | `sum(rate(...)) by (status)` | 2xx/4xx/5xx split |
| **Log Volume by Level** | `sum by (level) (count_over_time(..., level=~"ERROR\|WARNING"}[5m]))` | Log error/warning trend |
| **Recent Errors** | `{namespace="llm-wiki"} \|~ "(?i)error\|exception\|traceback"` | Live error log tail |

Loki panels in **bold**.

#### Dashboard 2: Business — RAG Pipeline (`uid: llm-wiki-business`)

| Panel | Query | What to watch |
|-------|-------|---------------|
| Cache Hit Rate | `sum(rate(cache_hit_total[5m])) / (sum(rate(cache_hit_total[5m])) + sum(rate(cache_miss_total[5m])))` | 3-tier cache effectiveness |
| LLM Tokens / Hour | `sum(increase(llm_tokens_used_total[1h])) by (model)` | Cost tracking |
| Query Count / min | `sum(rate(query_total{status="success"}[1m]))` | Throughput |
| Avg Synthesis Latency | `rate/rate` on synthesis histogram | LLM performance |
| Pipeline Stage Durations (P95) | `histogram_quantile` on 4 stages | Bottleneck detection |
| **Query Error Logs** | `{service="backend-v2"} \|~ "(?i)error\|exception"` | Live query error tail |

#### Dashboard 3: Ingestion — Pipeline (`uid: llm-wiki-ingestion`)

| Panel | Query | What to watch |
|-------|-------|---------------|
| CPU Worker Outcomes | `sum(rate(ingestion_jobs_total{stage=~"extract\|cpu_done"}[1h])) by (status)` | Classifier success/failure |
| Wiki Consumer Outcomes | `sum(rate(ingestion_jobs_total{stage=~"wiki\|embed"}[1h])) by (status)` | Wiki integration success |
| Queue Depth | `ingestion_queue_depth` | Backpressure signal |
| Job Duration (p95) | `histogram_quantile` by stage | Per-stage performance |
| Worker Heartbeat Age | `time() - worker_heartbeat_age_seconds` | Worker liveness — alert at >120s |
| Worker CPU % | `worker_cpu_percent` | Resource usage |
| **Worker Error Log Volume** | `sum by (service) (count_over_time({service=~"cpu-worker\|wiki-consumer", level="ERROR"} [5m]))` | Worker error trend |
| **Recent Worker Errors** | `{service=~"cpu-worker\|wiki-consumer"} \|~ "(?i)error\|exception\|traceback"` | Live worker error tail |

---

## 4. Pillar 2 — Logging

### 4.1 How logs flow

```
Python logging.getLogger().info(...)
        │
        ▼
Filters inject service, trace_id, span_id, worker_id  ← logging_config.py
        │
        ▼
JsonFormatter emits structured JSON to stderr  ← when LOG_FORMAT=json
        │
        ▼
Container stdout → CRI log file on host  ← /var/log/pods/llm-wiki_*/*/*.log
        │
        ▼
Promtail DaemonSet tails all pod logs  ← cri: {} + json: {} pipeline stages
        │  extracts: level, service, worker_id, trace_id as labels/metadata
        ▼
Loki StatefulSet stores in TSDB  ← 7-day retention, persistent PVC 1Gi
        │
        ▼
Grafana Explore + dashboard panels  ← LogQL queries
```

### 4.2 Application-level setup

Every service calls `setup_logging()` once at startup in `__main__`:

```python
from llm_wiki.infrastructure.telemetry.logging_config import setup_logging
from llm_wiki.config import settings

# backend (main.py)
setup_logging(
    service_name="backend-v2",
    log_format=settings.log_format,
    log_level=settings.log_level,
)

# cpu_worker
setup_logging(
    service_name="cpu-worker",
    log_format=settings.log_format,
    log_level=settings.log_level,
    worker_id=settings.worker_id,
)

# wiki_consumer
setup_logging(
    service_name="wiki-consumer",
    log_format=settings.log_format,
    log_level=settings.log_level,
    worker_id=settings.consumer_id,
)
```

### 4.3 Filters (order of execution on every log record)

| Filter | Injects | Source |
|--------|---------|--------|
| `ServiceNameFilter` | `record.service` | Static string from `setup_logging()` arg |
| `TraceIdFilter` | `record.trace_id`, `record.span_id` | `contextvars.ContextVar` from LangSmith adapter |
| `WorkerIdFilter` | `record.worker_id` | Pre-resolved from `settings.worker_id` / `settings.consumer_id` |

All filters use **lazy imports** and **try/except** — if LangSmith is not available, `trace_id`/`span_id` stay `null` without breaking logging.

### 4.4 Structured log fields (JSON mode)

When `LOG_FORMAT=json`, every log line is a JSON object:

```json
{
  "timestamp": "2026-07-30T04:15:00.123Z",
  "level": "INFO",
  "logger": "llm_wiki.application.use_cases.query.pipeline",
  "service": "backend-v2",
  "worker_id": null,
  "trace_id": "4d0fc7a4-e29b-41d4-a716-446655440000",
  "span_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "request handled",
  "method": "GET",
  "path": "/api/query",
  "status_code": 200,
  "elapsed_ms": 342.5
}
```

The `method`, `path`, `status_code`, `elapsed_ms` fields come from `RequestLoggingMiddleware` via `logger.info("request handled", extra={...})`. The `JsonFormatter` merges any `extra` dict keys automatically.

The `exception` field appears only when `exc_info=True` is passed to the logger (i.e., `logger.error("...", exc_info=True)`).

### 4.5 Promtail pipeline

The Promtail config applies two stages:

1. **`cri: {}`** — parses the containerd/docker CRI log format (timestamp + content)
2. **`json: {expressions: {level, service, worker_id, trace_id}}`** — extracts structured fields from the JSON body
3. **`labels: {app, level, service}`** — promotes low-cardinality fields to Loki stream labels for fast filtering

`trace_id` and `worker_id` are **not** promoted to labels — they are high-cardinality and would degrade Loki performance. Instead they stay as structured metadata accessible via LogQL filter expressions.

### 4.6 Querying logs (LogQL cookbook)

Open **Grafana → Explore → Loki** and use these queries:

```logql
# ── Basic filtering ──────────────────────────────────

# All logs from a service (label-based — fast)
{service="backend-v2"}

# Error logs only (label-based — fast)
{level="ERROR"}

# Logs from a specific pod
{pod="backend-v2-85f4b686c5-5dtv4"}

# ── Text search ──────────────────────────────────────

# Search for a trace_id copied from LangSmith UI
{service="backend-v2"} |= "4d0fc7a4-"

# Case-insensitive regex search
{namespace="llm-wiki"} |~ "(?i)timeout|connection refused"

# ── JSON field filters ───────────────────────────────

# Slow HTTP requests (>1 second)
{service="backend-v2"} | json | elapsed_ms > 1000

# Errors for a specific worker
{service="cpu-worker"} | json | worker_id=1 | level="ERROR"

# ── Aggregation ──────────────────────────────────────

# Error count by service (last 1 hour)
sum(count_over_time({namespace="llm-wiki", level="ERROR"} [1h])) by (service)

# Log volume per minute by service
sum by (service) (rate({namespace="llm-wiki"} [1m]))

# HTTP 5xx rate over 5 minutes
sum by (path) (rate({service="backend-v2"} | json | status_code >= 500 [5m]))

# ── Specific patterns ────────────────────────────────

# Database connection errors
{namespace="llm-wiki"} |~ "(?i)connection refused|operationalerror|could not connect"

# LLM API failures
{service="backend-v2"} |~ "(?i)openai.*error|api.*timeout|402|429|unauthorized"

# Worker job failures
{service=~"cpu-worker|wiki-consumer"} |~ "(?i)failed|error processing job"
```

### 4.7 Adding structured fields to your own logs

```python
import logging
logger = logging.getLogger(__name__)

# Pass structured data via `extra` dict — JsonFormatter merges automatically
logger.info(
    "cache lookup completed",
    extra={
        "cache_key": video_id,
        "cache_hit": True,
        "cache_level": "semantic",
        "lookup_ms": 12.3,
    },
)

# For errors, always include exc_info=True — JsonFormatter captures the traceback
try:
    await some_operation()
except Exception:
    logger.error("operation failed", extra={"entity_id": entity.id}, exc_info=True)
```

---

## 5. Pillar 3 — Tracing

All tracing goes through LangSmith. See `docs/telemetry-implementation-strategy.md` for full details on the wrapper pattern, parent-child linking, and trace tree structure.

### 5.1 Core design

The tracing system follows the same Port/Adapter pattern as metrics:

```
application/ports/telemetry/telemetry_port.py  ← ABC: TelemetryPort
infrastructure/telemetry/langsmith_telemetry_adapter.py  ← Real implementation
infrastructure/telemetry/null_telemetry_adapter.py       ← No-op (when disabled)
infrastructure/telemetry/__init__.py                     ← Factory
```

External service wrappers (LLM, embedding, search, cache) are wrapped in traced proxies that emit spans before/after each call. Business logic never mentions spans — it just calls `self._llm.chat_completion(...)` and the wrapper handles observability.

### 5.2 Trace tree: Query pipeline

```
rag_query (chain)                        ← root span
├── cache_check (chain)                  ← exact → semantic cache
│   ├── cache_get (cache)
│   └── embedding (embedding)
├── query_analyze (chain)                ← intent, keywords, entities, time_range
├── embedding (embedding)                ← embed question
├── vector_search (retriever)            ← pgvector HNSW
├── keyword_search (retriever)           ← tsvector full-text
├── event_search (retriever)             ← event observations
├── event_keyword_search (retriever)
├── rerank (chain)                       ← RRF merge + LLM scoring
│   └── llm_chat_completion_reasoning
└── llm_chat_completion_reasoning (llm)  ← final answer synthesis
```

### 5.3 Trace tree: Ingestion pipeline (2 workers, 2 root traces)

```
cpu_worker: process_cpu_job (chain)      ← root span
├── transcript extraction (not traced)
├── llm_chat_completion_raw (llm)        ← classifier
└── embedding (embedding)                ← summary vector
        │ queue → Redis
        ▼
wiki_consumer: process_wiki_job (chain)  ← root span
├── llm_chat_completion_reasoning ×3     ← 3-pass wiki integrator
├── llm_chat_completion (llm)            ← event extraction
├── embedding (embedding) × N            ← knowledge retrieval
└── section_embedding (embedding)        ← bge-m3 × N sections
```

### 5.4 How trace_id bridges to logs

Inside `LangSmithTelemetryAdapter.start_span()`:
```python
_current_trace_id.set(str(run.id))    # contextvars, async-safe
_current_span_id.set(span_id)         # application span UUID
```

Inside `TraceIdFilter.filter()`:
```python
record.trace_id = get_current_trace_id()
record.span_id = get_current_span_id()
```

This means every log line emitted **inside a LangSmith span** automatically carries the `trace_id`. The reverse is also true: if no span is active (e.g., startup, health check), both fields are `null`.

### 5.5 Access & navigation

1. Go to https://smith.langchain.com → project `llm-wiki-rag`
2. Click any trace named `rag_query`, `process_cpu_job`, or `process_wiki_job`
3. **Trace View** shows the parent-child tree — expand nodes to drill into children
4. **Timeline** tab shows a waterfall chart for bottleneck detection
5. Click any node to see: Inputs, Outputs, Metadata (`latency_ms`, `tokens_used`), Error (if failed)

| Goal | LangSmith Action |
|------|-----------------|
| See all query traces | Filter: `run_type = chain`, search `rag_query` |
| Find slow queries | Sort by Latency descending |
| Find failures | Filter: `Error = True` |
| Check cache hit rate | Search `cache_get`, scan outputs for `hit: true/false` |
| Correlate with logs | Copy `trace_id` from metadata → Grafana Explore → `{service="backend-v2"} \|= "<trace_id>"` |

---

## 6. Pillar 4 — Alerting

### 6.1 How alerts flow

```
Prometheus evaluates PromQL rules every 15s
Loki Ruler evaluates LogQL rules continuously
        │
        ▼
AlertManager receives alerts from both sources
        │
        ├── Routes by severity: critical → separate receiver, warning → separate receiver
        ├── Groups alerts by [alertname, severity]
        ├── Deduplicates (group_interval: 5m)
        ├── Repeats every 4h for ongoing alerts
        │
        ▼
    null receiver ← wire Telegram or Slack for production
```

### 6.2 Prometheus alert rules (8 rules)

**File:** `k8s/monitoring/prometheus-alert-rules.yaml`

#### Application alerts

| Alert | Severity | Trigger | For | What to do |
|-------|----------|---------|-----|------------|
| **HighErrorRate** | 🔴 critical | HTTP 5xx > 5% over 5m | 5m | Check backend logs: `kubectl logs -n llm-wiki deploy/backend-v2 --tail=100` |
| **QueryLatencyHigh** | 🟡 warning | P95 query latency > 30s | 15m | Check LLM provider + Ollama embedding latency |
| **WorkerStalled** | 🔴 critical | Worker heartbeat age > 120s | 2m | `kubectl logs -n llm-wiki deploy/cpu-worker -c cpu-worker` |
| **QueueBacklogHigh** | 🟡 warning | CPU queue depth > 100 | 10m | Check worker capacity; consider scaling replicas |
| **LLMApiErrorRate** | 🔴 critical | LLM API error rate > 5% | 5m | Check API key quota + provider status |
| **LLMApiErrorsSpike** | 🟡 warning | >10 LLM errors in 5m | 2m | Check provider status + rate limits |

#### Infrastructure alerts

| Alert | Severity | Trigger | For | What to do |
|-------|----------|---------|-----|------------|
| **PostgresDown** | 🔴 critical | `up{job="postgres"} == 0` | 1m | `kubectl get pods -n llm-wiki -l app=postgres` |
| **RedisDown** | 🟡 warning | `up{job="redis"} == 0` | 1m | Cache degraded; app works on DB fallback |
| **DiskSpaceLow** | 🔴 critical | Root FS < 10% free | 5m | Clean old logs/containers or expand disk |

### 6.3 Loki alert rules (3 rules)

**File:** `k8s/monitoring/loki-alert-rules.yaml`

These complement Prometheus alerts by catching **text patterns** metrics cannot see:

| Alert | Severity | Trigger | For | What to do |
|-------|----------|---------|-----|------------|
| **HighErrorLogRate** | 🟡 warning | Error logs > 0.05 lines/s | 5m | Grafana Explore → `{namespace="llm-wiki"} \|= "ERROR"` |
| **ServiceCrashLoop** | 🔴 critical | Crash/shutdown/fatal/panic in logs | — | `kubectl get pods -n llm-wiki \| grep -v Running` |
| **WorkerProcessingErrorSpike** | 🟡 warning | Worker error rate > 0.05 lines/s | 10m | Check Ingestion dashboard → Recent Worker Errors |

The Loki Ruler is configured in `loki-statefulset.yaml` with:
- `rule_path: /etc/loki/rules` (mounted from `loki-alert-rules` ConfigMap)
- `alertmanager_url: http://alertmanager.llm-wiki.svc.cluster.local:9093`
- `enable_api: true`

### 6.4 Viewing alert status

```bash
# List all Prometheus alert rules and their current state
kubectl -n llm-wiki exec deploy/prometheus -- wget -qO- http://localhost:9090/api/v1/rules

# Which Prometheus alerts are currently firing
kubectl -n llm-wiki exec deploy/prometheus -- wget -qO- http://localhost:9090/api/v1/alerts

# List all Loki alert rules
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://loki.llm-wiki.svc.cluster.local:3100/loki/api/v1/rules

# Check AlertManager for all firing alerts (both sources)
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://alertmanager.llm-wiki.svc.cluster.local:9093/api/v2/alerts
```

### 6.5 Wiring Telegram notifications

The current AlertManager uses **null receivers** (alerts fire silently). To wire Telegram:

1. Create a bot via [@BotFather](https://t.me/BotFather), get the token
2. Get your group chat ID
3. Edit `k8s/monitoring/alertmanager-config.yaml` — uncomment the `slack_configs` or add `telegram_configs`
4. Store the token in `k8s/secret.yaml` (do NOT commit):
   ```yaml
   TELEGRAM_BOT_TOKEN: "<token>"
   TELEGRAM_ALERT_CHAT_ID: "-1001234567890"
   ```
5. Update the `route` to use `receiver: 'telegram'`
6. Re-apply:
   ```bash
   kubectl apply -f k8s/monitoring/alertmanager-config.yaml
   kubectl -n llm-wiki rollout restart deploy/alertmanager
   ```

---

## 7. Incident Response

### 7.1 Workflow: "An alert fired — what now?"

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Alert fires (Slack/Telegram notification)                    │
│    ↓                                                            │
│ 2. Open Grafana → relevant dashboard                            │
│    - RED dashboard for HTTP alerts                              │
│    - Business dashboard for RAG pipeline alerts                 │
│    - Ingestion dashboard for worker alerts                      │
│    ↓                                                            │
│ 3. Narrow the time range to ±15 minutes around the alert        │
│    ↓                                                            │
│ 4. Check correlated panels:                                     │
│    - HighErrorRate → check Recent Errors log panel              │
│    - WorkerStalled → check Worker Heartbeat Age + CPU %        │
│    - LLMApiErrorRate → check Query Error Logs panel             │
│    ↓                                                            │
│ 5. For log-based alerts: Grafana Explore → Loki                │
│    {service="affected-service", level="ERROR"}                 │
│    ↓                                                            │
│ 6. For trace-level investigation:                               │
│    - Copy trace_id from a Loki log line                         │
│    - Open LangSmith → search by trace_id                        │
│    - Walk the span tree to identify the failing step            │
│    ↓                                                            │
│ 7. Apply fix, verify in dashboards, mark alert resolved         │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 Scenario: Backend 5xx spike

1. **HighErrorRate** alert fires → open RED dashboard
2. Error Rate panel confirms spike at the alert time
3. **Recent Errors** panel shows the actual error messages
4. Copy a `trace_id` from Loki → paste in LangSmith
5. LangSmith trace tree shows the failing step (e.g., `llm_chat_completion_reasoning` returned a timeout)
6. Fix: scale API keys, adjust timeouts, or fail over to fallback model

### 7.3 Scenario: Worker stalled

1. **WorkerStalled** alert fires → open Ingestion dashboard
2. Worker Heartbeat Age panel shows which worker (`worker_id`) is stuck
3. Queue Depth panel shows growing backlog
4. Check worker logs: `kubectl -n llm-wiki logs deploy/cpu-worker -c cpu-worker --tail=100`
5. For wiki-consumer: check Redis queue via logs; check for wiki API errors in Loki: `{service="wiki-consumer", level="ERROR"}`
6. Common causes: YouTube API rate limit (402/429), wiki page edit conflict, OOM kill

### 7.4 Scenario: Slow queries

1. No alert fires (latency is below 30s threshold) but users report slowness
2. Open Business dashboard → Pipeline Stage Durations (P95)
3. Identify which stage is the bottleneck (embedding? search? LLM synthesis?)
4. Open LangSmith → sort by Latency descending → open a slow trace
5. Timeline tab shows the exact wall-clock breakdown
6. Fix: increase cache size, add index, switch embedding model, etc.

### 7.5 Scenario: Log-based crash detection

1. **ServiceCrashLoop** alert fires (panic/fatal in logs)
2. Open Grafana → Explore → Loki
3. Query: `{namespace="llm-wiki"} |~ "(?i)panic|fatal|crash|killed"`
4. Filter by time range matching the alert
5. Identify the service and the crash reason from the log message
6. Check pod status: `kubectl -n llm-wiki get pods | grep -v Running`
7. Check events: `kubectl -n llm-wiki get events --sort-by=.lastTimestamp | tail -20`
8. Check pod restart count: `kubectl -n llm-wiki get pods -o wide`

---

## 8. Troubleshooting

### 8.1 Grafana shows "No data"

```bash
# 1. Verify Prometheus datasource: Grafana → Connections → Data Sources → Prometheus → Save & Test
# 2. Test the PromQL query directly in Prometheus: http://localhost:9090 → Graph → paste query
# 3. Expand time range — dashboards default to "Last 1 hour"
# 4. Check Prometheus is scraping targets:
kubectl -n llm-wiki exec deploy/prometheus -- wget -qO- http://localhost:9090/api/v1/targets | python3 -m json.tool
```

### 8.2 Prometheus target is DOWN

```bash
# Check specific target details
kubectl -n llm-wiki exec deploy/prometheus -- wget -qO- http://localhost:9090/api/v1/targets | python3 -m json.tool

# Verify pod has prometheus.io annotations
kubectl -n llm-wiki get pod <pod-name> -o yaml | grep -A3 prometheus.io

# Test the metrics endpoint directly
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://<target-ip>:<port>/metrics
```

### 8.3 Loki shows no log streams

```bash
# 1. Check Promtail for errors
kubectl -n llm-wiki logs -l app=promtail --tail=50 | grep -i error

# 2. Check Loki readiness
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://loki.llm-wiki.svc.cluster.local:3100/ready

# 3. Check available stream labels
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://loki.llm-wiki.svc.cluster.local:3100/loki/api/v1/label

# 4. Verify Promtail positions are advancing
kubectl -n llm-wiki exec -l app=promtail -- cat /tmp/positions.yaml | wc -l
```

Common root cause: CRI format mismatch. Kind uses containerd — the Promtail config must have `pipeline_stages: - cri: {}` not `docker: {}`.

### 8.4 Worker metrics return empty (Content-Length: 0)

```bash
# 1. Verify ENABLE_METRICS is true in the pod
kubectl -n llm-wiki exec deploy/cpu-worker -c cpu-worker -- python3 -c "import os; print(os.environ.get('ENABLE_METRICS'))"

# 2. If using an old Docker image, rebuild and reload:
docker build --network=host -t 32_llm_wiki_clean_arch-backend:latest .
kind load docker-image 32_llm_wiki_clean_arch-backend:latest --name llm-wiki
kubectl -n llm-wiki rollout restart deploy/cpu-worker
kubectl -n llm-wiki rollout restart statefulset/wiki-consumer
```

### 8.5 Postgres/Redis exporter crashes

```bash
# Check exporter logs
kubectl -n llm-wiki logs postgres-0 -c postgres-exporter --tail=20
kubectl -n llm-wiki logs deploy/redis -c redis-exporter --tail=20

# Common causes:
# - DATA_SOURCE_URI format wrong: must be localhost:5432/db?sslmode=disable&user=...
# - DATA_SOURCE_PASS secret key not present in env
# - redis-exporter cannot connect to localhost:6379
```

### 8.6 Logs are not in JSON format

```bash
# Check LOG_FORMAT in the pod
kubectl -n llm-wiki exec deploy/backend-v2 -- python3 -c "import os; print(os.environ.get('LOG_FORMAT'))"
kubectl -n llm-wiki exec deploy/cpu-worker -c cpu-worker -- python3 -c "import os; print(os.environ.get('LOG_FORMAT'))"

# Verify the log line format
kubectl -n llm-wiki logs deploy/backend-v2 --tail=3
# Expected JSON: {"timestamp":"...","level":"INFO","service":"backend-v2",...}
# If you see plain text: %(asctime)s ..., LOG_FORMAT is not set or not "json"
```

### 8.7 trace_id is always null in logs

1. Verify `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` is set
2. `trace_id` only populates inside an active LangSmith span — startup/shutdown logs have `null`
3. Worker logs: workers create root spans per job — `trace_id` is populated only during `process_cpu_job` / `process_wiki_job`, not during idle heartbeat cycles
4. Check that `setup_logging()` was called (not `logging.basicConfig()`)

### 8.8 AlertManager not sending to Telegram

```bash
# Check AlertManager health
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s http://alertmanager.llm-wiki.svc.cluster.local:9093/-/healthy

# Post a test alert to verify the pipeline works
kubectl -n llm-wiki exec deploy/backend-v2 -- curl -s -X POST \
  http://alertmanager.llm-wiki.svc.cluster.local:9093/api/v2/alerts \
  -H 'Content-Type: application/json' \
  -d '[{"labels":{"alertname":"TestAlert","severity":"warning"},"annotations":{"summary":"Manual test","description":"Testing the AlertManager pipeline"}}]'

# If the test alert appears in curl -s .../api/v2/alerts, the pipeline works.
# The null receivers are why nothing is delivered — wire Telegram/Slack per Section 6.5.
```

---

## 9. K8s Manifest Map

```
k8s/
├── configmap.yaml                     ← ENABLE_METRICS, LOG_FORMAT, LOG_LEVEL
├── backend/deployment.yaml            ← prometheus.io annotations + env injection
├── cpu-worker/deployment.yaml         ← prometheus.io annotations + health :8101
├── wiki-consumer/statefulset.yaml     ← prometheus.io annotations + health :8201
├── postgres/
│   ├── statefulset.yaml               ← postgres-exporter sidecar :9187
│   └── service.yaml                   ← port 9187 exposed for scraping
├── redis/
│   ├── deployment.yaml                ← redis-exporter sidecar :9121
│   └── service.yaml                   ← port 9121 exposed for scraping
├── minio/
│   └── statefulset.yaml               ← prometheus.io scrape annotation, auth=public
└── monitoring/
    ├── prometheus-rbac.yaml           ← ServiceAccount + ClusterRole + Binding
    ├── prometheus-config.yaml         ← scrape_configs (pod SD + static)
    ├── prometheus-deployment.yaml     ← Deployment (emptyDir tsdb)
    ├── prometheus-alert-rules.yaml    ← 8 PromQL alert rules
    ├── grafana-datasources.yaml       ← Prometheus + Loki datasources
    ├── grafana-dashboards.yaml        ← 3 dashboards + 5 Loki panels
    ├── grafana-deployment.yaml        ← Deployment (emptyDir)
    ├── alertmanager-config.yaml       ← null receivers (placeholder)
    ├── alertmanager-deployment.yaml   ← Deployment
    ├── loki-statefulset.yaml          ← StatefulSet + 1Gi PVC + ruler + 7d retention
    ├── loki-alert-rules.yaml          ← 3 LogQL alert rules (Loki Ruler)
    └── promtail-daemonset.yaml        ← DaemonSet (cri + json pipeline stages)
```

---

## 10. Code Reference

### 10.1 Application code (Python)

| File | Purpose |
|------|---------|
| `application/ports/telemetry/telemetry_port.py` | `TelemetryPort` ABC + `TelemetrySpan` dataclass |
| `application/ports/telemetry/metrics_port.py` | `MetricsPort` ABC |
| `infrastructure/telemetry/__init__.py` | `create_telemetry_adapter()` factory |
| `infrastructure/telemetry/langsmith_telemetry_adapter.py` | LangSmith `RunTree` implementation + `_current_trace_id`/`_current_span_id` ContextVars |
| `infrastructure/telemetry/null_telemetry_adapter.py` | No-op fallback when tracing disabled |
| `infrastructure/telemetry/logging_config.py` | `JsonFormatter`, `ServiceNameFilter`, `TraceIdFilter`, `WorkerIdFilter`, `setup_logging()` |
| `infrastructure/telemetry/prometheus_metrics_adapter.py` | Prometheus `Counter`/`Histogram`/`Gauge` adapter |
| `infrastructure/telemetry/null_metrics_adapter.py` | No-op fallback when metrics disabled |
| `infrastructure/telemetry/metrics_collector.py` | `get_metrics()` singleton factory |
| `infrastructure/telemetry/business_metrics.py` | `inc_counter()`, `track_duration()`, `set_gauge()` helpers |
| `presentation/middleware/metrics_middleware.py` | FastAPI RED middleware with path normalization |
| `presentation/middleware/request_logging.py` | Structured HTTP request logging via `extra` dict |
| `presentation/routes/metrics.py` | `GET /api/metrics` → Prometheus text format |
| `infrastructure/entrypoints/health_server.py` | Worker `/metrics` endpoint (when `ENABLE_METRICS=true`) |
| `config.py` | `enable_metrics`, `log_format`, `log_level`, `langsmith_tracing_enabled` |

### 10.2 Infrastructure (K8s + scripts)

| File | Purpose |
|------|---------|
| `k8s/monitoring/loki-statefulset.yaml` | Loki StatefulSet + PVC + ConfigMap + ruler |
| `k8s/monitoring/loki-alert-rules.yaml` | 3 LogQL alert rules |
| `k8s/monitoring/promtail-daemonset.yaml` | Promtail DaemonSet + CRI + JSON pipeline |
| `k8s/monitoring/prometheus-config.yaml` | Scrape configs (pod SD + static targets) |
| `k8s/monitoring/prometheus-alert-rules.yaml` | 8 PromQL alert rules |
| `k8s/monitoring/grafana-dashboards.yaml` | 3 dashboards (RED, Business, Ingestion) with PromQL + Loki panels |
| `k8s/monitoring/grafana-datasources.yaml` | Prometheus + Loki datasource definitions |
| `k8s/monitoring/alertmanager-config.yaml` | Alert group/route config (null receivers) |
| `k8s/configmap.yaml` | `ENABLE_METRICS`, `LOG_FORMAT`, `LOG_LEVEL` |
| `scripts/deploy-monitoring.sh` | One-command full stack deploy |

### 10.3 Documentation

| File | Purpose |
|------|---------|
| `docs/observability-guide.md` | **This document** — complete observability reference |
| `docs/telemetry-implementation-strategy.md` | LangSmith tracing deep-dive (wrapper pattern, span trees) |
