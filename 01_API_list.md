# LLM Wiki - Danh sách API (v2.0.0)

Dự án được xây dựng theo Clean Architecture với FastAPI. Tất cả API đều có prefix `/api`.

---

## 1. Health Check

### `GET /api/health`
- **Tag:** health
- **Mô tả:** Kiểm tra trạng thái hoạt động của service. Trả về trạng thái `ok`, phiên bản API, và số lượng item đang chờ xử lý.
- **Response:** `{"status": "ok", "version": "2.0.0", "db": "connected", "pending_count": N, "requires_membership_count": N, "failed_count": N}`

---

## 2. Query (Hỏi đáp - RAG Pipeline)

### `POST /api/query`
- **Tag:** query
- **Mô tả:** Gửi câu hỏi và nhận câu trả lời từ LLM dựa trên dữ liệu wiki đã index. Pipeline xử lý gồm: cache check → embed câu hỏi → vector search (pgvector) → keyword search (tsvector) → reciprocal rank fusion (kết hợp kết quả) → LLM sinh câu trả lời → cache lưu kết quả.
- **Request Body:**
  - `question` (string, required): Câu hỏi của người dùng.
  - `source_id` (string, optional): Lọc tìm kiếm theo nguồn dữ liệu cụ thể (YouTube channel...).
  - `top_k` (int, default=10): Số lượng kết quả tìm kiếm trả về tối đa.
  - `from_date` (datetime, optional): Lọc câu trả lời từ ngày này (ISO 8601).
  - `to_date` (datetime, optional): Lọc câu trả lời đến ngày này (ISO 8601).
- **Response:**
  - `answer` (string): Câu trả lời được LLM sinh ra.
  - `citations` (list[dict]): Danh sách nguồn tham khảo gồm `page_title`, `page_slug`, `section`, `source_name`, `source_url`, `timestamp`.
  - `sources_used` (list[dict]): Tên các nguồn được sử dụng.
  - `tokens_used` (int): Số token đã dùng.
  - `latency_ms` (float): Tổng thời gian xử lý (ms).

### `POST /api/query/stream`
- **Tag:** query
- **Mô tả:** Giống `POST /api/query` nhưng trả về kết quả dạng Server-Sent Events (SSE) streaming. LLM sẽ trả về từng token một theo thời gian thực. Hỗ trợ `from_date`/`to_date`.
- **Request Body:** Giống `POST /api/query` (tham số `stream` luôn được set thành `true`).
- **Response:** SSE stream với các event (đã được route chuyển đổi sang frontend shape):
  - `metadata`: Thông tin pipeline steps timing + `trace_id` (nếu LangSmith bật).
  - `token`: Một phần (token) của câu trả lời.
  - `complete`: Kết thúc stream, kèm `citations` và `sources_used`.

### `GET /api/summarize`
- **Tag:** query
- **Mô tả:** Tóm tắt các sự kiện và trang wiki trong một khoảng thời gian.
- **Query Parameters:**
  - `days` (int, default=30, min=1, max=365): Số ngày lùi lại từ hiện tại.
- **Response:**
  - `summary` (string): Bản tóm tắt.
  - `time_range` (dict): `start`, `end`.
  - `stats` (dict): `event_count`, `page_count`, `items_completed`, `items_failed`, `items_rate_limited`.
  - `top_events` (list[dict]): Các sự kiện nổi bật.
  - `top_pages` (list[dict]): Các trang nổi bật.

---

## 3. Sources (Quản lý nguồn dữ liệu)

### `POST /api/sources`
- **Tag:** sources
- **Mô tả:** Tạo mới một nguồn dữ liệu (ví dụ: một kênh YouTube) để hệ thống theo dõi và ingest nội dung.
- **Request Body:**
  - `name` (string, required): Tên hiển thị của nguồn.
  - `platform` (string, default="youtube"): Nền tảng (youtube, ...).
  - `external_id` (string, required): ID của nguồn trên nền tảng gốc (VD: YouTube channel ID).
  - `url` (string, required): URL của nguồn.
- **Response:** Thông tin source đã tạo gồm `id`, `name`, `platform`, `external_id`, `url`, `status`.

### `GET /api/sources`
- **Tag:** sources
- **Mô tả:** Lấy danh sách tất cả các nguồn dữ liệu đang active (chưa bị xóa mềm).
- **Response:** Mảng các object source gồm `id`, `name`, `platform`, `external_id`, `url`, `status`, `added_at`, `last_checked_at`.

---

## 4. Pages (Trang wiki)

### `GET /api/pages/{slug}`
- **Tag:** pages
- **Mô tả:** Lấy nội dung chi tiết của một trang wiki theo slug (đường dẫn thân thiện). Slug được sinh tự động từ tiêu đề trang.
- **Parameters:** `slug` (path, required) - slug của trang cần lấy.
- **Response:** Object page gồm `id`, `title`, `slug`, `content_markdown` (nội dung đầy đủ dạng markdown), `summary`, `domain`, `key_entities`, `status`, `created_at`, `updated_at`.

### `GET /api/pages`
- **Tag:** pages
- **Mô tả:** Lấy danh sách các trang wiki, có thể lọc theo nguồn hoặc phân trang.
- **Query Parameters:**
  - `source_id` (string, optional): Lọc trang theo nguồn dữ liệu.
  - `limit` (int, default=50): Số lượng trang tối đa trả về.
  - `offset` (int, default=0): Vị trí bắt đầu lấy dữ liệu (cho phân trang).
- **Response:** `items` (list) gồm `id`, `title`, `slug`, `status`, `created_at` + `total` (tổng số).

---

## 5. Search (Tìm kiếm)

### `GET /api/search`
- **Tag:** search
- **Mô tả:** Tìm kiếm toàn văn (full-text search) sử dụng PostgreSQL tsvector. Trả về các đoạn nội dung khớp với từ khóa, kèm điểm relevance score và recency boost.
- **Query Parameters:**
  - `q` (string, required): Từ khóa tìm kiếm.
  - `limit` (int, default=20): Số lượng kết quả tối đa.
- **Response:** `results` (list: `id`, `title`, `slug`, `summary`, `source_name`, `published_at`, `score`) + `total`.

---

## 6. Progress & Monitoring (Theo dõi tiến độ)

> Các API dưới đây thuộc router `stubs`, chỉ được kích hoạt khi biến môi trường `ENABLE_STUB_ROUTES=true`. Đây là các API phục vụ admin dashboard.

### `GET /api/progress`
- **Tag:** stubs
- **Mô tả:** Trả về tổng quan tiến độ ingestion trên toàn hệ thống. Bao gồm:
  - `global`: Thống kê toàn cục theo từng trạng thái (pending, pending_transcribe, waiting_for_wiki, processing, done_today, failed, rate_limited, requires_membership).
  - `per_source`: Tiến độ chi tiết cho từng nguồn (name, done, total, percent).
  - `alerts`: 20 log ingestion gần nhất (lỗi, rate limit...).
  - `processing_items`: Danh sách các item đang được xử lý kèm thời gian đã chạy.
  - `requires_membership_count`: Số lượng video yêu cầu membership để xem.

### `GET /api/system-stats`
- **Tag:** stubs
- **Mô tả:** Trả về thông tin tài nguyên hệ thống máy chủ (sử dụng thư viện `psutil`).
- **Response:**
  - `cpu_percent`: Phần trăm CPU đang sử dụng.
  - `ram_used_gb` / `ram_total_gb`: RAM đã dùng / tổng (GB).
  - `disk_used_gb` / `disk_total_gb`: Ổ đĩa đã dùng / tổng (GB).

---

## 7. Source Detail (Chi tiết nguồn dữ liệu)

### `GET /api/sources/{source_id}`
- **Tag:** stubs
- **Mô tả:** Lấy thông tin chi tiết của một nguồn dữ liệu, bao gồm thống kê trạng thái của tất cả video/item thuộc nguồn đó.
- **Parameters:** `source_id` (path, UUID, required).
- **Response:** Chi tiết source + `video_count`, `page_count`, `status_breakdown` (đếm theo từng trạng thái: pending, processing, completed, failed, no_captions, skipped, rate_limited), `config`.

### `PATCH /api/sources/{source_id}`
- **Tag:** stubs
- **Mô tả:** Cập nhật một phần thông tin của nguồn dữ liệu. Hiện tại là stub - chỉ trả về thông tin hiện tại mà chưa thực sự cập nhật gì.
- **Parameters:** `source_id` (path, UUID, required).

### `DELETE /api/sources/{source_id}`
- **Tag:** stubs
- **Mô tả:** Xóa mềm (soft delete) một nguồn dữ liệu - set `status = "inactive"`. Không xóa dữ liệu thực tế trong DB.
- **Parameters:** `source_id` (path, UUID, required).
- **Response:** `{"status": "deleted", "id": "<source_id>"}`

### `POST /api/sources/{source_id}/scan`
- **Tag:** stubs
- **Mô tả:** Kích hoạt quét nguồn dữ liệu để tìm video mới. Hiện tại là stub - trả về thông báo "Scan triggered (not yet implemented)".
- **Parameters:** `source_id` (path, UUID, required).
- **Response:** `status`, `message`, `new_items_found`, `restarted_rate_limited`, `restarted_failed`.

---

## 8. Source Items (Quản lý item/video trong nguồn)

### `GET /api/sources/{source_id}/items`
- **Tag:** stubs
- **Mô tả:** Lấy danh sách các item (video) thuộc một nguồn dữ liệu, có thể lọc theo trạng thái.
- **Parameters:**
  - `source_id` (path, UUID, required).
  - `status` (query, optional): Lọc theo trạng thái, có thể truyền nhiều trạng thái cách nhau bằng dấu phẩy (VD: `failed,no_captions`).
- **Response:** `items` (list) gồm `id`, `source_id`, `external_id`, `title`, `url`, `published_at`, `status`, `retry_count`, `priority`, `error_message`, `created_at` + `total`.

### `POST /api/sources/items/{item_id}/skip`
- **Tag:** stubs
- **Mô tả:** Đánh dấu bỏ qua (skip) một item - set `status = "skipped"`, xóa `error_message`. Dùng khi admin muốn bỏ qua một video không cần xử lý.
- **Parameters:** `item_id` (path, UUID, required).

### `POST /api/sources/items/{item_id}/retry`
- **Tag:** stubs
- **Mô tả:** Thử lại (retry) một item đã fail/skip - reset về `status = "pending"`, xóa `error_message`, tăng `retry_count` lên 1, xóa `retry_after`.
- **Parameters:** `item_id` (path, UUID, required).

### `POST /api/sources/items/{item_id}/transcript`
- **Tag:** stubs
- **Mô tả:** Nộp transcript thủ công cho một item. Hiện tại là stub - chưa xử lý body request, chỉ trả về acknowledge.
- **Parameters:** `item_id` (path, UUID, required).

---

## 9. Page Detail (Chi tiết trang wiki - nâng cao)

### `GET /api/pages/{slug}`
- **Tag:** stubs
- **Lưu ý:** Route này trùng với `GET /api/pages/{slug}` từ `pages.py`. Router nào đăng ký trước trong `main.py` sẽ được ưu tiên (hiện tại `pages.router` đăng ký trước).
- **Mô tả:** Lấy chi tiết đầy đủ của một trang wiki, bao gồm:
  - Thông tin cơ bản: `id`, `title`, `slug`, `content_markdown`, `summary`, `status`, `created_at`, `updated_at`, `published_at`.
  - `source_name` / `source_url` / `source_video_url`: Thông tin nguồn gốc.
  - `sections`: Các section của trang (được tách từ markdown headings) kèm `section_order`, `title`, `content_markdown`, `source_ref`.
  - `media_assets`: Các file media đính kèm (ảnh, ...) gồm `filename`, `minio_path`, `mime_type`.
  - `linked_pages`: Các trang wiki được liên kết tới trang này, kèm `relation_type`.

### `PATCH /api/pages/{page_id}`
- **Tag:** stubs
- **Mô tả:** Cập nhật một phần nội dung trang wiki. Hiện tại là stub - chỉ trả về thông tin hiện tại.
- **Parameters:** `page_id` (path, UUID, required).

---

## 10. Graph (Đồ thị tri thức)

### `GET /api/graph`
- **Tag:** stubs
- **Mô tả:** Trả về đồ thị liên kết giữa các trang wiki (page-link graph). Mỗi node là một trang, mỗi edge là một liên kết giữa hai trang.
- **Query Parameters:**
  - `source_id` (string, optional): Lọc đồ thị theo nguồn dữ liệu.
  - `limit` (int, default=100): Số lượng link tối đa.
  - `offset` (int, default=0): Vị trí bắt đầu.
- **Response:** `nodes` (list: id, title, source_name) + `edges` (list: from, to, relation_type).

### `GET /api/entity-graph`
- **Tag:** stubs
- **Mô tả:** Trả về đồ thị thực thể (entity graph) - các thực thể được trích xuất từ nội dung wiki và mối quan hệ giữa chúng.
- **Query Parameters:**
  - `entity_type` (string, optional): Lọc theo loại thực thể (VD: person, organization, location...).
  - `predicate` (string, optional): Lọc theo loại quan hệ.
  - `depth` (int, optional): Độ sâu duyệt đồ thị.
  - `limit` (int, default=200, hoặc 10000 nếu full_graph=true): Số lượng tối đa.
  - `entity_id` (string, optional): Lọc quanh một thực thể cụ thể.
  - `full_graph` (string, optional): Nếu `"true"`, tăng limit lên 10000 để lấy toàn bộ đồ thị.
- **Response:** `nodes` (list: id, label, type, ticker, event_count) + `edges` (list: source, target, edge_type, predicate, confidence).

### `GET /api/cluster-graph`
- **Tag:** stubs
- **Mô tả:** Trả về đồ thị phân cụm thực thể theo loại (entity type). Mỗi node là một cluster đại diện cho một loại thực thể, kích thước dựa trên số lượng thực thể trong cluster đó. Chỉ tính các entity có ít nhất một relation.
- **Response:** `clusters` (list: id=entity_type, label, type="cluster", event_count=số lượng) + `edges` (list: source_cluster, target_cluster, weight).

### `GET /api/cluster-expand`
- **Tag:** stubs
- **Mô tả:** Mở rộng một cluster - trả về danh sách thực thể thuộc một loại cụ thể và các `entity_relations` nội bộ của cluster đó.
- **Query Parameters:**
  - `entity_type` (string, optional): Loại thực thể cần mở rộng.
  - `limit` (int, default=500): Số lượng tối đa.
- **Response:** `nodes` (list: id, label, type, ticker, event_count) + `edges` (list: source, target, edge_type="entity_relation", predicate, confidence).

---

## 11. Attention Items (Các mục cần chú ý)

### `GET /api/attention-items`
- **Tag:** stubs
- **Mô tả:** Lấy danh sách các item đang ở trạng thái lỗi hoặc cần sự can thiệp của admin (failed, no_captions, no_captions_t3_fail, skipped, requires_membership).
- **Query Parameters:**
  - `page` (int, default=1): Số trang.
  - `per_page` (int, default=100): Số item mỗi trang.
- **Response:** `items` (list: id, video_id, title, status, error_message, source_name, created_at) + `total`, `page`, `per_page`.

---

## 12. Workers (Theo dõi worker xử lý)

### `GET /api/workers`
- **Tag:** stubs
- **Mô tả:** Lấy danh sách các worker đang hoạt động trong hệ thống (các process xử lý ingestion). Thông tin được lấy từ bảng `worker_heartbeat`.
- **Response:** Mảng `workers` gồm:
  - `worker_id`: ID của worker.
  - `status`: Trạng thái hiện tại (idle, processing...).
  - `alive` (bool): `true` nếu heartbeat gần nhất trong vòng 120 giây.
  - `heartbeat_ago_secs`: Số giây từ lần heartbeat cuối.
  - `current_job_id`: Job hiện tại đang xử lý.
  - `current_stage`: Giai đoạn xử lý hiện tại (transcribe, wiki, event_extract).
  - `stage_duration_secs`: Thời gian đã xử lý ở giai đoạn hiện tại.
  - `cpu_percent`: Phần trăm CPU worker đang dùng.
  - `error_message`: Thông báo lỗi nếu có.

---

## 13. Restart (Khởi động lại item lỗi)

### `POST /api/restart/{item_id}`
- **Tag:** stubs
- **Mô tả:** Reset một item về trạng thái `pending` để xử lý lại từ đầu. Xóa `error_message` và `retry_after`.
- **Parameters:** `item_id` (path, UUID, required).

### `POST /api/restart/source/{source_id}`
- **Tag:** stubs
- **Mô tả:** Khởi động lại hàng loạt tất cả các item lỗi (failed, no_captions, rate_limited, skipped) của một nguồn dữ liệu. Set tất cả về `pending`.
- **Parameters:** `source_id` (path, UUID, required).
- **Response:** `{"status": "ok", "restarted": <số lượng đã restart>}`

---

## 14. Admin API Keys (Quản lý khóa API)

### `GET /api/admin/api-keys`
- **Mô tả:** Lấy danh sách tất cả API keys đã đăng ký trong hệ thống (dùng để gọi các LLM provider như OpenAI, v.v.). Key được mask (hiển thị `***` + 4 ký tự cuối).
- **Response:** Mảng các key gồm `id`, `provider`, `api_key_masked`, `model_name`, `status`, `priority`, `rate_limited_until`, `usage_count`, `last_used_at`, `created_at`, `updated_at`.

### `POST /api/admin/api-keys`
- **Mô tả:** Tạo mới một API key. Trùng `provider` + `api_key` → `409`.
- **Request:** `{ "provider": "opencode"|"gemini", "api_key": str, "model_name": str, "priority": int }`.
- **Response:** `201` với object key đã serialize (gồm `api_key_masked`).

### `PUT /api/admin/api-keys/{key_id}`
- **Mô tả:** Cập nhật `status`, `priority`, `model_name` của một API key. Không tồn tại → `404`.
- **Parameters:** `key_id` (path, UUID, required).
- **Response:** Object key đã serialize.

### `DELETE /api/admin/api-keys/{key_id}`
- **Mô tả:** Xóa vĩnh viễn một API key khỏi database. Không thể xóa key `active` cuối cùng → `409`.
- **Parameters:** `key_id` (path, UUID, required).
- **Response:** `{"status": "ok", "deleted": 1}`

### `POST /api/admin/api-keys/{key_id}/activate`
- **Mô tả:** Kích hoạt lại một API key đang bị rate-limited/disabled. Set `status = "active"` và xóa `rate_limited_until`.
- **Parameters:** `key_id` (path, UUID, required).
- **Response:** Object key sau khi activate.

---

## 15. Admin Cron Jobs (Quản lý tác vụ định kỳ)

### `GET /api/admin/cron-jobs`
- **Tag:** stubs
- **Mô tả:** Lấy danh sách tất cả cron job đã đăng ký trong hệ thống (VD: quét YouTube định kỳ, backup database...). Trạng thái được xác định thực tế từ K8s CronJob/Job và worker heartbeats.
- **Response:** Mảng các job gồm `job_id`, `name`, `description`, `schedule` (cron expression), `job_type`, `managed`, `status`, `last_run`, `crontab_active`, `alive_workers`, `error`.
- **Status values:**
  - `scheduled`: CronJob tồn tại và không bị suspend.
  - `running`: Một child Job đang chạy.
  - `error`: Job gần nhất failed.
  - `stopped`: `cron_jobs.enabled = false` hoặc CronJob bị suspend.
  - `not_found`: Không tìm thấy CronJob trong namespace.
  - `no_workers`: Background task không có worker heartbeat gần đây.

### `POST /api/admin/cron-jobs/{job_id}/start`
- **Tag:** stubs
- **Mô tả:** Bật (enable) một cron job - set `enabled = True`.
- **Parameters:** `job_id` (path, string, required).

### `POST /api/admin/cron-jobs/{job_id}/stop`
- **Tag:** stubs
- **Mô tả:** Tắt (disable) một cron job - set `enabled = False`.
- **Parameters:** `job_id` (path, string, required).

---

## 16. Admin Clear Alerts (Xóa cảnh báo)

### `DELETE /api/admin/clear-alerts`
- **Tag:** stubs
- **Mô tả:** Xóa tất cả ingestion log có `event_type` là `error` hoặc `rate_limit` khỏi database.
- **Query Parameters:**
  - `all` (string, optional): (hiện chưa dùng đến).
- **Response:** `{"status": "ok", "deleted": <số bản ghi đã xóa>}`

---

## 17. Chat Sessions (Phiên chat - Stub)

> Tất cả API chat sessions hiện đều là stub, chưa có persistence thực sự.

### `GET /api/chat/sessions`
- **Tag:** stubs
- **Mô tả:** Lấy danh sách phiên chat. **Hiện luôn trả về mảng rỗng `[]`.**

### `POST /api/chat/sessions`
- **Tag:** stubs
- **Mô tả:** Tạo phiên chat mới. **Trả về object stub** với `id="stub-1"`.

### `GET /api/chat/sessions/{session_id}`
- **Tag:** stubs
- **Mô tả:** Lấy chi tiết một phiên chat. **Trả về object stub.**

### `PUT /api/chat/sessions/{session_id}`
- **Tag:** stubs
- **Mô tả:** Cập nhật phiên chat. **Trả về object stub.**

### `DELETE /api/chat/sessions/{session_id}`
- **Tag:** stubs
- **Mô tả:** Xóa phiên chat. **Trả về `{"status": "deleted"}`.**

---

## Tổng kết

| Nhóm | Số lượng API | Trạng thái |
|---|---|---|
| Health | 1 | Hoàn chỉnh |
| Query (RAG) | 3 | Hoàn chỉnh (`/query`, `/query/stream`, `/summarize`) |
| Sources cơ bản | 2 | Hoàn chỉnh |
| Pages cơ bản | 2 | Hoàn chỉnh |
| Search | 1 | Hoàn chỉnh |
| Progress & Monitoring | 2 | Stub route |
| Source Detail | 4 | 1 hoàn chỉnh, 3 stub |
| Source Items | 4 | 2 hoàn chỉnh, 2 stub |
| Page Detail nâng cao | 2 | Stub route |
| Graph (Knowledge Graph) | 4 | Stub route; entity-graph/cluster-expand trả về dữ liệu thực |
| Attention Items | 1 | Stub route |
| Workers | 1 | Stub route |
| Restart | 2 | Stub route |
| Admin API Keys | 5 | 3 hoàn chỉnh, 2 stub (501) |
| Admin Cron Jobs | 3 | Stub route; `/admin/cron-jobs` trả về trạng thái thực từ K8s |
| Admin Clear Alerts | 1 | Stub route |
| Chat Sessions | 5 | Toàn bộ là stub |
| **Tổng** | **43** | |

### Kiến trúc RAG Pipeline (`POST /api/query`)

```
User Question
    │
    ▼
[1. Time Range Extraction] → regex patterns (Vietnamese/English) + optional LLM fallback
    │
    ▼
[2. Cache Check] ─── hit ──→ Trả về cached answer
    │ miss
    ▼
[3. Embed Question] → Ollama Embedding
    ▼
[4. Vector Search] → pgvector (semantic similarity + time filter + recency boost)
    │
[5. Keyword Search] → tsvector (full-text match + time filter + recency boost)
    │
    ▼
[6. Reciprocal Rank Fusion] → Kết hợp & xếp hạng kết quả
    │
    ▼
[7. LLM Synthesis] → OpenAI-compatible LLM (sinh câu trả lời từ context)
    │
    ▼
[8. Telemetry Span] → LangSmith (nếu bật) với latency, tokens, sources
    │
    ▼
[9. Cache Save] → Redis (TTL 3600s)
    │
    ▼
Response (answer + citations + sources_used + tokens_used + latency_ms)
```

### Ghi chú

- Các API trong router `stubs` (`stubs.py`) chỉ được kích hoạt khi biến môi trường `ENABLE_STUB_ROUTES=true`. Đây là các API phục vụ admin dashboard.
- Một số stub route đã có logic thực: `/api/entity-graph`, `/api/cluster-expand` truy vấn DB thực; `/api/admin/cron-jobs` đọc trạng thái từ K8s API.
- Route `GET /api/pages/{slug}` bị trùng giữa `pages.py` và `stubs.py`. Router đăng ký trước (`pages.py`) được ưu tiên, trả về dữ liệu cơ bản. Router `stubs.py` có phiên bản chi tiết hơn (kèm sections, media, linked_pages) nhưng bị ghi đè.
- `/api/admin/*` routes được mount vĩnh viễn trong `main.py` (không phụ thuộc `ENABLE_STUB_ROUTES`) vì CronJob K8s cần gọi chúng.
- Hệ thống sử dụng Clean Architecture: `presentation/routes/` → `application/use_cases/` → `domain/` ← `infrastructure/`.
