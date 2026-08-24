# Wiki Extraction v2 — Rollout & Rollback Runbook

Áp dụng cho các phase P2 (chunking), P3 (finance schema + migration), P4 (reflect), P5 (normalization). Toàn bộ feature nằm sau flags default OFF (`WIKI_CHUNKING_ENABLED`, `WIKI_WRITE_THINKING_ENABLED`, `WIKI_REFLECT_ENABLED` — xem `config.py`). Mỗi stage rollout tuân theo trình tự dưới đây.

## Nguyên tắc

- **Migration TRƯỚC code** (chỉ P3): `k8s/migrations/002_add_pass1_facts.sql` phải chạy trước khi deploy code ghi `item.pass1_facts`.
- **Python changes không cần rebuild image**: các pod mount hostPath `/code/backend-src` → `/app/src` (k8s/wiki-consumer/deployment.yaml:174-178). Sync source vào node rồi restart pod.
- **Rollback = tắt flag** (không revert code), trừ khi cần rollback nội dung → restore snapshot.

## Thứ tự rollout (mỗi stage)

```
1. PAUSE intake:        kubectl scale deployment wiki-consumer -n llm-wiki --replicas=0
2. (P3) MIGRATION:      kubectl exec -n llm-wiki postgres-0 -- psql -U wiki -d llm_wiki \
                          -v lock_timeout=5000 -f - < k8s/migrations/002_add_pass1_facts.sql
                        VERIFY:
                          SELECT column_name FROM information_schema.columns
                          WHERE table_name='source_items' AND column_name='pass1_facts';
                        → phải trả 'pass1_facts'. Nếu không → STOP, không deploy code.
3. SYNC SOURCE:         rsync -a --delete src/llm_wiki/ <node>:/code/backend-src/llm_wiki/
                        (pod chạy trong kind node; nếu không truy cập node được →
                         rebuild image theo scripts/deploy-k8s.sh)
4. RESTART:             kubectl rollout restart deployment -n llm-wiki backend-v2 cpu-worker wiki-consumer
                        kubectl rollout status deployment -n llm-wiki wiki-consumer
5. BẬT FLAG (stage):    kubectl set env deployment/wiki-consumer -n llm-wiki \
                          WIKI_CHUNKING_ENABLED=true   # ví dụ stage P2
                        kubectl rollout restart deployment -n llm-wiki wiki-consumer
                        VERIFY env: kubectl exec -n llm-wiki deploy/wiki-consumer -- env | grep WIKI_
6. CANARY:              python scripts/reprocess-wiki.py --external-id <VIDEO_ID> --generation v2
                        (chọn 1 item đã biết chất lượng — ví dụ u45c_nVV0Sk)
7. GATE (thresholds):   kiểm tra log/LangSmith của canary:
   - Parse failure rate ≤ 10%        (log: "JSON parse failed" / total calls)
   - Reflect error rate ≤ 10%        (log: "Reflect & Verify failed")
   - Facts/item ≥ baseline           (numbers/events count ≥ 17/5 cho video BĐS dài)
   - Token tăng ≤ 3x                 (usage trong span; không đo được → log tokens)
   - Không timeout 3600s             (job budget 3600s — canary-verified; log "wiki integrate timed out")
   → PASS: mở rộng batch 20 → 100; RESUME queue: scale replica=1+
   → FAIL: ROLLBACK (dưới)
8. RESUME:              kubectl scale deployment wiki-consumer -n llm-wiki --replicas=1
```

## Rollback

```
1. TẮT FLAG:      kubectl set env deployment/wiki-consumer -n llm-wiki WIKI_<FEATURE>=false
                  kubectl rollout restart deployment -n llm-wiki wiki-consumer
2. RESTORE SRC:   rsync lại source cũ (git checkout <prev>) + restart 3 workloads
3. NỘI DUNG:      nếu page hỏng do canary → restore snapshot (giữ 7 ngày, P6):
                  - chọn PageSnapshot mới nhất trước canary (page_snapshots.created_at)
                  - khôi phục sections_jsonb/content_markdown (script ad-hoc qua psql)
4. MIGRATION:     KHÔNG drop cột pass1_facts (nullable, vô hại; code cũ không đọc)
```

## Flags

| Flag | Mặc định | Stage |
|------|----------|-------|
| `WIKI_CHUNKING_ENABLED` | false | P2 |
| `WIKI_WRITE_THINKING_ENABLED` | false | P1 (opt-in, chưa bật mặc định) |
| `WIKI_REFLECT_ENABLED` | false | P4 |

Lưu ý: `REASONING_ENABLED` (global, default true) KHÔNG dùng làm switch wiki.

## Canary đã xác nhận (2026-08-24)

- `u45c_nVV0Sk` — TOÀN CẢNH BĐS VN (69K chars): baseline 17 numbers / 5 claims → chunked v2: **126 numbers / 54 events / 50 claims** (7.4x / 10.8x / 10x); page 13 sections có citation fact_id; `pass1_facts` persist đủ 7 finance arrays.
- Job timeout 1800s → 3600s; per-chunk timeout 240s → 300s; reflect timeout 300s → 600s (đều canary-verified: API latency ~200-300s/chunk, reflect context ~190-240K chars).
- Bug canary phát hiện: `_call_llm_json` retry path thiếu `return` (trả None → `NoneType.get`); API restart thiếu reset `retry_count` (sweeper fail sớm) — đã fix + regression test.

## Observability

- LangSmith run metadata: `pipeline_generation`, `chunking`, `write_thinking`, `reflect` (wiki_consumer process_wiki_job)
- Counters: `ingestion_jobs_total{stage}`, `llm_pass_calls_total{pass,status}` (wiki_integrator)
- Structured log per job: "Pass 1 OK ..." / "Pass 2 OK ..." / "Reflect: N corrections applied" / chunk counts
