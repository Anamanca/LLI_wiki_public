// --- Source Management ---

export interface Source {
  id: string;
  name: string;
  platform: string;
  external_id: string;
  url: string;
  added_at: string;
  last_checked_at: string | null;
  status: "active" | "inactive";
  config: Record<string, unknown>;
}

export interface CreateSourcePayload {
  name: string;
  platform: string;
  external_id: string;
  url: string;
  config?: Record<string, unknown>;
}

export interface UpdateSourcePayload {
  name?: string;
  platform?: string;
  external_id?: string;
  url?: string;
  status?: "active" | "inactive";
  config?: Record<string, unknown>;
}

export interface SourceDetail extends Source {
  video_count: number;
  page_count: number;
  status_breakdown: StatusBreakdown;
}

export interface StatusBreakdown {
  pending: number;
  processing: number;
  completed: number;
  failed: number;
  no_captions: number;
  skipped: number;
  rate_limited: number;
}

export interface SourceListResponse {
  sources: Source[];
  total: number;
}

// --- Wiki Pages ---

export interface PageSummary {
  id: string;
  title: string;
  slug: string;
  summary: string | null;
  source_name: string | null;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  published_at: string | null;
}

export interface PageListResponse {
  items: PageSummary[];
  total: number;
  page: number;
  per_page: number;
}

export interface PageSection {
  id: string;
  section_order: number;
  title: string | null;
  content_markdown: string | null;
  source_ref: string | null;
}

export interface PageMediaAsset {
  id: string;
  filename: string;
  minio_path: string;
  mime_type: string | null;
  url: string | null;
  description: string | null;
}

export interface LinkedPage {
  id: string;
  title: string;
  slug: string;
  relation_type: string;
}

export interface PageDetail {
  id: string;
  title: string;
  slug: string;
  content_markdown: string | null;
  summary: string | null;
  source_name: string | null;
  source_url: string | null;
  source_video_url: string | null;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  published_at: string | null;
  sections: PageSection[];
  media_assets: PageMediaAsset[];
  linked_pages: LinkedPage[];
}

// --- Chat / Query ---

export interface QueryRequest {
  question: string;
  session_id?: string;
  history?: { role: "user" | "assistant"; content: string }[];
  source_id?: string;
  top_k?: number;
  language?: string;
}

export interface Citation {
  page_title: string;
  page_slug: string;
  section: string;
  source_name: string;
  source_url: string;
  timestamp: string;
}

export interface SourceUsage {
  name: string;
  pages_used: number;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  sources_used: SourceUsage[];
  tokens_used: number;
  latency_ms: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  timestamp: string;
}

// --- Chat Sessions ---

export interface ChatSessionMeta {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatSession {
  id: string;
  title: string;
  messages: ChatMessage[];
  created_at: string;
  updated_at: string;
}

// --- Dashboard / Progress ---

export interface GlobalProgress {
  pending: number;
  pending_transcribe: number;
  waiting_for_wiki: number;
  processing: number;
  done_today: number;
  failed: number;
  rate_limited: number;
  requires_membership: number;
}

export interface SourceProgress {
  name: string;
  done: number;
  total: number;
  percent: number;
}

export interface IngestionAlert {
  id: string;
  event_type: string;
  message: string | null;
  source_item_id: string | null;
  created_at: string | null;
}

export interface ProcessingItem {
  id: string;
  video_id: string;
  title: string;
  stage: string;
  stage_label: string;
  started_at: string | null;
  elapsed_seconds: number;
  source_name: string;
}

export interface Progress {
  global: GlobalProgress;
  per_source: SourceProgress[];
  alerts: IngestionAlert[];
  processing_items: ProcessingItem[];
  requires_membership_count: number;
}

// --- Search ---

export interface SearchResult {
  id: string;
  title: string;
  slug: string;
  summary: string | null;
  source_name: string | null;
  published_at: string | null;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
}

// --- Graph ---

export interface GraphNode {
  id: string;
  title: string;
  source_name: string | null;
}

export interface GraphEdge {
  from: string;
  to: string;
  relation_type: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// NOTE: GraphNode/GraphEdge/GraphData above are for PAGE-LINK graphs (GET /api/graph).
// EntityGraphNode/EntityGraphEdge/EntityGraphData below are for ENTITY-RELATION graphs (GET /api/entity-graph).
// Do NOT mix these types — they have different field structures.

export interface EntityGraphNode {
  id: string;
  label: string;
  type: string;        // stock_ticker | person | market_index | macro_indicator | policy | location | commodity
  ticker: string | null;
  event_count: number;
}

export interface EntityGraphEdge {
  source: string;
  target: string;
  edge_type: string;   // "entity_relation" | "entity_event"
  predicate: string;
  confidence: number | null;
}

export interface EntityGraphData {
  nodes: EntityGraphNode[];
  edges: EntityGraphEdge[];
}

// --- Restart ---

export interface RestartResponse {
  status: string;
  item_id?: string;
  restarted: number;
}

export interface ScanResponse {
  status: string;
  message: string;
  new_items_found?: number;
  restarted_rate_limited?: number;
  restarted_failed?: number;
}

// --- Source Items (Needs Attention) ---

export interface SourceItem {
  id: string;
  source_id: string;
  external_id: string;
  title: string | null;
  url: string | null;
  published_at: string | null;
  status: string;
  retry_count: number;
  priority: number;
  error_message: string | null;
  created_at: string | null;
}

export interface SourceItemListResponse {
  items: SourceItem[];
  total: number;
}

export interface AttentionItem {
  id: string;
  video_id: string;
  title: string | null;
  status: string;
  error_message: string | null;
  source_name: string;
  created_at: string | null;
}

export interface AttentionItemsResponse {
  items: AttentionItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface TranscriptSubmitResponse {
  status: string;
  item_id: string;
  wiki_action?: string;
  wiki_page?: string;
}

export interface ManualTranscriptPayload {
  transcript_text: string;
}

// --- API ---

export interface ApiError {
  detail: string;
}

// --- API Keys ---

export interface ApiKeyRow {
  id: string;
  provider: string;
  api_key_masked: string;
  model_name: string;
  status: "active" | "rate_limited" | "disabled";
  priority: number;
  rate_limited_until: string | null;
  usage_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateApiKeyPayload {
  provider: string;
  api_key: string;
  model_name?: string;
  priority?: number;
}

export interface UpdateApiKeyPayload {
  status?: string;
  priority?: number;
  model_name?: string;
}

// --- Cron Jobs ---

export interface CronJobStatus {
  job_id: string;
  name: string;
  description: string;
  schedule: string;
  job_type: "kubernetes_cronjob" | "crontab" | "background_task";
  managed: boolean;
  status: string;
  last_run: string | null;
  crontab_active?: boolean;
  alive_workers?: number;
  error?: string;
}

export interface CronJobActionResponse {
  success: boolean;
  message?: string;
  error?: string;
  output?: string;
}

// --- Workers ---

export interface WorkerInfo {
  worker_id: number;
  status: string;
  alive: boolean;
  heartbeat_ago_secs: number;
  current_job_id: string | null;
  current_stage: string | null;
  stage_duration_secs: number;
  cpu_percent: number;
  error_message: string | null;
}

export interface WorkersResponse {
  workers: WorkerInfo[];
}
