# Investigation: Wiki Update Gap (July 16–20, 2026)

**Date:** 2026-07-20 02:35 UTC  
**Status:** Investigated — root cause confirmed

---

## Summary

Ko có bài wiki mới từ 17/07 đến nay là do **CronJob `youtube-daily-scan` bị fail vào ngày 18/07 và 19/07**. Scan không chạy → không có source_items mới → không có page wiki mới. Nguyên nhân gốc là CronJob dùng `wget` gọi endpoint CPU worker nhưng worker pod có restart + network transient → wget fail, backoffLimit=1 → job fail luôn. Ngoài ra, **936 video bị stuck ở trạng thái `requires_membership`** — content members-only không thể truy cập được.

---

## Evidence

### 1. Scan Lock History (bảng `scan_lock`)

| Date | Started | Completed | Notes |
|------|---------|-----------|-------|
| Jul 11 | 08:01 | ✓ | Cron OK |
| Jul 12 | 08:01 | ✓ | Cron OK |
| Jul 13 | 08:01 | ✓ | Cron OK |
| Jul 14 | 08:01 | ✓ | Cron OK |
| **Jul 15** | **MISSING** | — | **Cron fail** |
| Jul 16 | 13:07 | ✓ | **Manual trigger** (5h late, ai đó trigger tay) |
| Jul 17 | 08:01 | ✓ | Cron OK |
| **Jul 18** | **MISSING** | — | **Cron fail** |
| **Jul 19** | **MISSING** | — | **Cron fail** |
| Jul 20 | 08:01 | ✓ | Cron OK (today) |

### 2. Source Items Created Per Day

| Date | New Items | Done | Notes |
|------|-----------|------|-------|
| Jul 15 | 0 | — | No scan ran |
| Jul 16 | 6 | 6 | Manual scan at 13:07 |
| Jul 17 | 2 | 2 | Cron scan found 2 videos |
| **Jul 18** | **0** | — | **No scan ran** |
| **Jul 19** | **0** | — | **No scan ran** |
| Jul 20 | 2 (so far) | 2 | Today's scan, processing in progress |

### 3. Latest Wiki Pages

```
2026-07-17 08:09 — Dự thảo Luật Nhà ở sửa đổi 2026...
2026-07-16 14:18 — Áp lực tăng lãi suất Fed...
2026-07-16 14:11 — Nghịch lý Dòng tiền Doanh nghiệp...
2026-07-16 13:57 — Đề án 3168...
2026-07-14 08:07 — Ngày 13/07/2026: VN-Index giảm 27.8 điểm...
```

Pages mới nhất là 17/07 — khớp với claim của bạn.

### 4. CronJob Failures

```bash
youtube-daily-scan-29727421    Failed    9d ago    (Jul 11)
youtube-daily-scan-29740381    Failed    17h ago   (Jul 19)
youtube-daily-scan-29741821    Complete  115m ago  (Jul 20, today)
youtube-scan-test-1784182116   Failed    3d20h ago (manual test?)
```

### 5. CronJob Configuration

- **Schedule:** `1 1 * * *` (08:01 ICT daily)
- **concurrencyPolicy:** `Forbid` — nếu job cũ chưa xong, job mới bị skip
- **backoffLimit:** `1` — chỉ retry 1 lần rồi fail
- **Trigger method:** `wget -q -O - --post-data="" http://cpu-worker.llm-wiki.svc.cluster.local:8100/api/admin/cron-jobs/youtube-daily-scan/start`
- **failedJobsHistoryLimit:** `3` — chỉ giữ 3 job fail gần nhất, các job fail cũ hơn bị xóa

### 6. Cluster State (all pods healthy now)

```
backend-v2      RUNNING   1 restarts (94m ago)
cpu-worker      2/2 RUNNING   4 restarts (16h ago) ← notable
wiki-consumer-0 RUNNING   4 restarts (16h ago)
wiki-consumer-1 RUNNING   4 restarts (16h ago)
frontend        RUNNING   2 restarts (16h ago)
```

**Cluster-wide restart event ~16h ago** (around July 19 10:00 UTC / 17:00 ICT). Đây có thể là lý do scan ngày 19/07 fail — pod đang restart đúng lúc cron chạy.

### 7. BIG Issue: 936 Videos Stuck as `requires_membership`

```
per_source:
  TaiChinhKinhDoanh: 78.3% done (582 pending, mostly requires_membership)
  Thai_Pham:         59.5% done (964 pending, mostly requires_membership)
  YeuKinhTe:         96.2% done
  Quang_Dung_CK:     99.7% done
```

Đây là vấn đề nghiêm trọng hơn gap 4 ngày. **936 video members-only không thể process được** vì:
- YouTube không cho phép download/transcribe video private/members-only
- Không có cách nào tự động bypass được nếu không có membership access
- Các kênh Tài Chính Kinh Doanh và Thái Phạm đang ngày càng chuyển content sang members-only

---

## Root Causes (theo priority)

### 1. CronJob Intermittently Fails (Critical)

**Evidence:** 3/7 ngày gần đây (Jul 15, 18, 19) scan không chạy.

**Why:**
- CronJob dùng `wget` từ busybox — không có retry logic, timeout handling
- `backoffLimit: 1` — fail 1 lần là job fail, không retry thêm
- `failedJobsHistoryLimit: 3` — job fail bị xóa sau 3 lần, khó trace
- CPU worker pod restart (4 lần trong 3d20h) → endpoint 8100 không available đúng lúc cron chạy
- Không có alert/monitoring khi cron fail → chỉ phát hiện khi user than phiền

### 2. No Monitoring / Alerting (High)

Không ai biết scan fail cho đến khi user check. Cần:
- Health check endpoint báo cáo `last_scan_date`
- Alert (Telegram?) khi scan_date > 1 ngày trước
- Dashboard hiển thị trạng thái scan gần nhất

### 3. No Backfill Mechanism (High)

Khi scan fail 1 ngày, không có cách tự động backfill. `scan_lock` cho ngày đó không tồn tại, và không có job nào chạy sau đó để bù. Cơ chế scan hiện tại chỉ scan incremental (video mới từ lần scan trước), nên nếu miss 1 ngày, video ngày đó bị bỏ qua vĩnh viễn.

### 4. 936 Requires Membership Videos (Ongoing)

Đây là giới hạn của YouTube platform, không phải bug. Nhưng ảnh hưởng đáng kể đến coverage.

---

## Recommendations

### Ngắn hạn (hôm nay)
1. **Sửa CronJob** — thêm retry/timeout logic robust hơn:
   ```yaml
   command:
   - sh
   - -c
   - |
     for i in 1 2 3; do
       code=$(wget -q -O - --timeout=30 --post-data="" \
         http://cpu-worker.llm-wiki.svc.cluster.local:8100/api/admin/cron-jobs/youtube-daily-scan/start 2>&1; echo $?)
       if [ "$code" = "0" ]; then echo "OK"; exit 0; fi
       sleep 30
     done
     exit 1
   ```
   Hoặc chuyển sang dùng `curl` thay vì `wget` (busybox wget hạn chế).

2. **Backfill scan các ngày miss** — trigger scan thủ công qua admin UI. Nhưng check xem scan logic có backfill được cho các ngày trước không, vì current implementation là incremental.

### Trung hạn (tuần này)
3. **Thêm alert** — Telegram bot notification khi `last_scan_date > 1 day ago`
4. **Dashboard health widget** — hiển thị "Last scan: X days ago" + trạng thái
5. **Tăng `backoffLimit` lên 3** và `failedJobsHistoryLimit` lên 7 để dễ debug
6. **Xem xét migrate scan logic vào trong pod** thay vì CronJob — dùng internal scheduler (`APScheduler` hoặc `asyncio` loop) để tránh phụ thuộc vào K8s CronJob reliability

### Dài hạn
7. **Migrate ingestion code từ `29_LLM_wiki` sang clean architecture repo này** — hiện tại K8s vẫn mount source từ project cũ
8. **Members-only content strategy** — hoặc có membership access, hoặc đánh dấu + skip để tập trung resource vào content accessible

---

## Unresolved Questions

- Tại sao Jul 16 scan được trigger manual lúc 13:07? Ai đã trigger? Có phải đã phát hiện gap và sửa nhưng không triệt để?
- Cluster restart event ~16h ago (Jul 19 ~10:00 UTC) — nguyên nhân là gì? OOM? Node restart? Có liên quan đến K8s cron failure?
- Scan logic trong legacy code có thực sự backfill được cho ngày miss không? Hay incremental-only?
