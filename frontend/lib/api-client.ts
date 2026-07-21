import type {
  Source,
  SourceDetail,
  SourceListResponse,
  CreateSourcePayload,
  UpdateSourcePayload,
  PageListResponse,
  PageDetail,
  QueryRequest,
  QueryResponse,
  Progress,
  SearchResponse,
  GraphData,
  EntityGraphData,
  RestartResponse,
  ScanResponse,
  SourceItemListResponse,
  SourceItem,
  TranscriptSubmitResponse,
  ManualTranscriptPayload,
  IngestionAlert,
  ChatSessionMeta,
  ChatSession,
  CronJobStatus,
  CronJobActionResponse,
  WorkerInfo,
  WorkersResponse,
} from "@/types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "/api";

async function request<T>(
  endpoint: string,
  options?: RequestInit
): Promise<T> {
  const url = `${API_BASE}${endpoint}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const errorText = await res.text().catch(() => "Unknown error");
    throw new Error(`${res.status}: ${errorText}`);
  }

  return res.json();
}

// --- Sources ---

export function fetchSources(platform?: string, status?: string) {
  const params = new URLSearchParams();
  if (platform) params.set("platform", platform);
  if (status) params.set("status", status);
  const qs = params.toString();
  return request<SourceListResponse>(`/sources${qs ? `?${qs}` : ""}`);
}

export function fetchSource(id: string) {
  return request<SourceDetail>(`/sources/${id}`);
}

export function createSource(payload: CreateSourcePayload) {
  return request<Source>("/sources", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateSource(id: string, payload: UpdateSourcePayload) {
  return request<Source>(`/sources/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteSource(id: string) {
  return request<void>(`/sources/${id}`, {
    method: "DELETE",
  });
}

export function scanSource(id: string) {
  return request<ScanResponse>(`/sources/${id}/scan`, {
    method: "POST",
  });
}

// --- Source Items ---

export function fetchSourceItems(sourceId: string, status?: string) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  const qs = params.toString();
  return request<SourceItemListResponse>(`/sources/${sourceId}/items${qs ? `?${qs}` : ""}`);
}

export function skipSourceItem(itemId: string) {
  return request<{ status: string; item_id: string; message: string }>(
    `/sources/items/${itemId}/skip`,
    { method: "POST" }
  );
}

export function retrySourceItem(itemId: string) {
  return request<{ status: string; item_id: string; message: string }>(
    `/sources/items/${itemId}/retry`,
    { method: "POST" }
  );
}

export function submitManualTranscript(itemId: string, payload: ManualTranscriptPayload) {
  return request<TranscriptSubmitResponse>(`/sources/items/${itemId}/transcript`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// --- Progress ---

export function fetchProgress() {
  return request<Progress>("/progress");
}

// --- Admin ---

export function clearAlerts() {
  return request<{ status: string; deleted: number }>("/admin/clear-alerts", {
    method: "DELETE",
  });
}

// --- Restart ---

export function restartSource(sourceId: string) {
  return request<RestartResponse>(`/restart/source/${sourceId}`, {
    method: "POST",
  });
}

export function restartItem(itemId: string) {
  return request<RestartResponse>(`/restart/${itemId}`, {
    method: "POST",
  });
}

// --- Wiki Pages ---

export function fetchPages(params?: {
  page?: number;
  per_page?: number;
  source_id?: string;
  search?: string;
  sort_by?: string;
  sort_order?: string;
}) {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  if (params?.source_id) sp.set("source_id", params.source_id);
  if (params?.search) sp.set("search", params.search);
  if (params?.sort_by) sp.set("sort_by", params.sort_by);
  if (params?.sort_order) sp.set("sort_order", params.sort_order);
  const qs = sp.toString();
  return request<PageListResponse>(`/pages${qs ? `?${qs}` : ""}`);
}

export function fetchPage(slug: string) {
  return request<PageDetail>(`/pages/${slug}`);
}

export function updatePage(id: string, payload: { content_markdown?: string; summary?: string }) {
  return request<PageDetail>(`/pages/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

// --- Search ---

export function searchPages(query: string, limit = 20) {
  return request<SearchResponse>(`/search?q=${encodeURIComponent(query)}&limit=${limit}`);
}

// --- Graph ---

export function fetchGraph(sourceId?: string, limit = 100, offset = 0) {
  const params = new URLSearchParams();
  if (sourceId) params.set("source_id", sourceId);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return request<GraphData>(`/graph?${params.toString()}`);
}

// --- Entity Graph (Knowledge Graph) ---

export function fetchEntityGraph(params?: {
  entity_type?: string;
  predicate?: string;
  depth?: number;
  limit?: number;
  entity_id?: string;
}) {
  const sp = new URLSearchParams();
  if (params?.entity_type) sp.set("entity_type", params.entity_type);
  if (params?.predicate) sp.set("predicate", params.predicate);
  if (params?.depth) sp.set("depth", String(params.depth));
  if (params?.limit) sp.set("limit", String(params.limit));
  if (params?.entity_id) sp.set("entity_id", params.entity_id);
  const qs = sp.toString();
  return request<EntityGraphData>(`/entity-graph${qs ? `?${qs}` : ""}`);
}

export function fetchFullEntityGraph(predicate?: string) {
  const sp = new URLSearchParams();
  sp.set("full_graph", "true");
  if (predicate) sp.set("predicate", predicate);
  return request<EntityGraphData>(`/entity-graph?${sp.toString()}`);
}

export function fetchClusterExpand(entityType: string, limit = 300) {
  const sp = new URLSearchParams();
  sp.set("entity_type", entityType);
  sp.set("limit", String(limit));
  return request<EntityGraphData>(`/cluster-expand?${sp.toString()}`);
}

// --- Chat Query ---

export function postQuery(payload: QueryRequest) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 90_000);
  return request<QueryResponse>("/query", {
    method: "POST",
    body: JSON.stringify(payload),
    signal: controller.signal,
  }).finally(() => clearTimeout(timeoutId));
}

// --- Chat Sessions ---

export function fetchChatSessions() {
  return request<ChatSessionMeta[]>("/chat/sessions");
}

export function createChatSession(title?: string) {
  return request<ChatSession>("/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export function fetchChatSession(id: string) {
  return request<ChatSession>(`/chat/sessions/${id}`);
}

export function saveChatSession(
  id: string,
  messages: { role: string; content: string }[],
  title?: string
) {
  const payload: { messages: typeof messages; title?: string } = { messages };
  if (title) payload.title = title;
  return request<ChatSession>(`/chat/sessions/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteChatSession(id: string) {
  return request<void>(`/chat/sessions/${id}`, {
    method: "DELETE",
  });
}

// --- Health ---

export function fetchHealth() {
  return request<{ status: string; db: string }>("/health");
}

// --- System Stats ---

export function fetchSystemStats() {
  return request<SystemStats>("/system-stats");
}

export interface SystemStats {
  cpu_percent: number;
  ram_used_gb: number;
  ram_total_gb: number;
  disk_used_gb: number;
  disk_total_gb: number;
}

// --- Attention Items ---

export function fetchAttentionItems(params?: {
  page?: number;
  per_page?: number;
}) {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.per_page) sp.set("per_page", String(params.per_page));
  const qs = sp.toString();
  return request<import("@/types").AttentionItemsResponse>(`/attention-items${qs ? `?${qs}` : ""}`);
}

// --- API Keys ---

export function fetchApiKeys() {
  return request<import("@/types").ApiKeyRow[]>("/admin/api-keys");
}

export function createApiKey(payload: import("@/types").CreateApiKeyPayload) {
  return request<import("@/types").ApiKeyRow>("/admin/api-keys", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateApiKey(id: string, payload: import("@/types").UpdateApiKeyPayload) {
  return request<import("@/types").ApiKeyRow>(`/admin/api-keys/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteApiKey(id: string) {
  return request<{ status: string; deleted: string }>(`/admin/api-keys/${id}`, {
    method: "DELETE",
  });
}

export function activateApiKey(id: string) {
  return request<import("@/types").ApiKeyRow>(`/admin/api-keys/${id}/activate`, {
    method: "POST",
  });
}

// --- Cron Jobs ---

export function fetchCronJobs() {
  return request<CronJobStatus[]>("/admin/cron-jobs");
}

export function startCronJob(jobId: string) {
  return request<CronJobActionResponse>(`/admin/cron-jobs/${jobId}/start`, {
    method: "POST",
  });
}

export function stopCronJob(jobId: string) {
  return request<CronJobActionResponse>(`/admin/cron-jobs/${jobId}/stop`, {
    method: "POST",
  });
}

// --- Workers ---

export function fetchWorkers() {
  return request<WorkersResponse>("/workers");
}
