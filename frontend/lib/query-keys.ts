export const queryKeys = {
  sources: {
    all: ["sources"] as const,
    detail: (id: string) => ["sources", id] as const,
  },
  pages: {
    all: ["pages"] as const,
    detail: (slug: string) => ["pages", slug] as const,
    filtered: (params?: Record<string, unknown>) => ["pages", "filtered", params] as const,
    search: (query: string) => ["search", query] as const,
  },
  progress: {
    all: ["progress"] as const,
  },
  graph: {
    all: ["graph"] as const,
    bySource: (sourceId?: string) => ["graph", sourceId] as const,
  },
  health: ["health"] as const,
  query: ["query"] as const,
  items: (sourceId: string) => ["items", sourceId] as const,
  systemStats: ["system-stats"] as const,
  attentionItems: {
    all: ["attention-items"] as const,
    filtered: (params?: Record<string, unknown>) => ["attention-items", params] as const,
  },
  apiKeys: {
    all: ["api-keys"] as const,
  },
  cronJobs: {
    all: ["cron-jobs"] as const,
  },
  workers: {
    all: ["workers"] as const,
  },
} as const;
