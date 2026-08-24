# LLM Wiki — Observability Guide (personal deployment)

> **Last updated:** 2026-08-23
> **Stack hiện tại:** LangSmith (tracing) · `scripts/healthcheck.sh` (liveness) · `kubectl logs`/`docker logs` (log)
> **Đã gỡ bỏ:** Prometheus · Grafana · Loki · Promtail · AlertManager (2026-08-23 — xem §6)

---

## 1. Kiến trúc

```
                        LangSmith (cloud, smith.langchain.com)
                        project: llm-wiki-rag
   ┌──────────────────────────┴──────────────────────────┐
   │ rag_query (query pipeline)                          │
   │   cache_check → guardrail → search ×4 → rerank → LLM│
   │ process_cpu_job (ingestion stage 1)                 │
   │   transcript_extract → llm classifier → embedding   │
   │ process_wiki_job (ingestion stage 2)                │
   │   3-pass wiki integrate → event extract → embeddings│
   └─────────────────────────────────────────────────────┘

   Healthcheck: scripts/healthcheck.sh (curl + kubectl + psql)
   Logs:        kubectl logs -n llm-wiki <pod> | docker logs <container>
   Worker state: worker_heartbeats (DB) → /api/admin/cron-jobs (frontend)
```

- **Tracing:** LangSmith ghi từng span với inputs/outputs/metadata (latency_ms, tokens_used, errors). Đây là nguồn duy nhất để phân tích luồng LLM từng bước.
- **Liveness:** `worker_heartbeats` trong Postgres + script healthcheck. Không cần Prometheus để biết worker sống/chết.
- **Log:** JSON structured với `trace_id`/`span_id` — grep theo trace id trong LangSmith để xem log của đúng request đó.

## 2. Truy cập (không cần port-forward)

Cơ chế: `k8s/kind-config.yaml` map host port 30080/30081 → kind node; frontend/backend là **NodePort services**. Truy cập trực tiếp từ localhost và Tailscale:

| Gì | URL |
|---|---|
| Frontend (toàn app) | `http://localhost:30080` / `http://100.115.181.93:30080` (Tailscale IP) |
| Backend API | `http://localhost:30081/api/...` |
| Backend health | `http://localhost:30081/api/health` |

- Frontend tự proxy `/api/*` → backend qua Next.js rewrites (trong cluster), nên chỉ cần 1 port 30080 cho toàn bộ app.
- Tailscale IP: `tailscale ip -4` (máy này `100.115.181.93`). Mọi thiết bị trong tailnet truy cập được.
- **Không còn** socat / kubectl port-forward / systemd service. Các service cũ đã disable: `llm-wiki-socat-forward.service` (user), `k8s-frontend-portforward.service` (system).

## 3. LangSmith — phân tích luồng LLM

### 3.1 Truy cập
`https://smith.langchain.com` → project **`llm-wiki-rag`** (dùng LANGSMITH_API_KEY trong k8s secret).

### 3.2 Trace của Query (`rag_query`)
Mỗi câu hỏi = 1 root span, cây con:

```
rag_query (chain)
├── cache_check → cache_get / cache_semantic_get
├── query_analyze (guardrail + intent)
├── embedding
├── vector_search / keyword_search / event_search / event_keyword_search
├── rerank → llm_chat_completion_reasoning
└── llm_chat_completion_stream (synthesis)
```

Xem gì: cache hit/miss (`outputs.cache_hit`), latency từng bước (`metadata.latency_ms`), tokens (`metadata.tokens_used`), kết quả search (`outputs.result_count`).

### 3.3 Trace của Ingestion (thu thập video)
**`process_cpu_job`** (cpu-worker):
```
process_cpu_job (chain)
├── transcript_extract (tool)      ← mới thêm 2026-08-23
│     inputs: video_id, cached_transcript
│     outputs: cached, language, duration_seconds, segments, raw_text_length
│     error:   extract: <msg> / extract transient: <msg> / no captions...
└── llm_chat_completion_raw (classifier: main_topic, language...)
```

**`process_wiki_job`** (wiki-consumer):
```
process_wiki_job (chain)
├── llm_chat_completion_reasoning ×3 (3-pass wiki integrate)
├── llm_chat_completion (event extraction)
├── embedding ×N (knowledge retrieval)
└── section_embedding (bge-m3 × sections, 4 concurrent)
```

Xem gì: thời gian extract transcript (bước đắt nhất — download/whisper), kết quả classifier, số segment, số section, token/chi phí từng pass, failure point (`error` field).

### 3.4 Giới hạn cần biết
- Free tier ~5k traces/tháng, retention ngắn. 1 video = 1 `process_cpu_job` + 1 `process_wiki_job` + N spans con. Nếu ingestion nhiều, theo dõi usage trên LangSmith.
- Bước **transcript download/whisper** nằm trong span `transcript_extract` (latency + error) nhưng chi tiết nội bộ (tier nào dùng, whisper segments) không tách span riêng — debug sâu thì xem log worker.

## 4. Healthcheck

```bash
./scripts/healthcheck.sh                # report 1 lần, exit 0 = OK
# Cron mỗi 10 phút:
# */10 * * * * /home/hieunt/32_LLM_wiki_clean_arch/scripts/healthcheck.sh >> /tmp/llm-wiki-health.log 2>&1
```

Kiểm tra: node cluster, readyReplicas của mọi workload, frontend `:30080`, backend `:30081/api/health`, worker heartbeat trong 120s (query trực tiếp Postgres), disk/memory host, CPU kind node.

## 5. Logs

```bash
kubectl -n llm-wiki logs deploy/backend-v2 -f
kubectl -n llm-wiki logs deploy/cpu-worker -f
kubectl -n llm-wiki logs deploy/wiki-consumer -f

# Trace một request cụ thể: copy trace_id từ LangSmith → grep log
kubectl -n llm-wiki logs deploy/backend-v2 --since=1h | grep "<trace_id>"
```

`LOG_FORMAT=json` vẫn bật — log có `trace_id`, `span_id`, `service`, `level`.

## 6. Đã gỡ bỏ (2026-08-23)

| Thành phần | Lý do gỡ |
|---|---|
| Prometheus + Grafana + 3 dashboards | App metrics trùng gần 100% dữ liệu LangSmith spans; data trên `emptyDir` (mất khi restart pod) |
| Loki + Promtail | 1 máy thì `kubectl logs` + grep trace_id là đủ |
| AlertManager + 11 alert rules | Thay bằng healthcheck script + DB heartbeat |
| postgres-exporter / redis-exporter sidecars | Chỉ phục vụ Prometheus |
| `k8s/monitoring/` (15 manifests) + 3 scripts | Xóa khỏi repo |

- `ENABLE_METRICS=false` trong configmap — `/api/metrics` không còn serve, code MetricsPort vẫn giữ (no-op khi tắt, có thể bật lại).
- Nếu muốn khôi phục: `git log` tìm commit trước 2026-08-23 để lấy lại `k8s/monitoring/` + scripts.
