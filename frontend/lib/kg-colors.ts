export const ENTITY_TYPE_COLORS: Record<string, string> = {
  stock_ticker: "#3b82f6",
  commodity: "#eab308",
  person: "#22c55e",
  market_index: "#a855f7",
  macro_indicator: "#14b8a6",
  policy: "#ef4444",
  location: "#f97316",
};

export const PREDICATE_COLORS: Record<string, string> = {
  is_subsidiary_of: "#22c55e",
  owns: "#22c55e",
  acquired_by: "#22c55e",
  merged_with: "#22c55e",
  competes_with: "#ef4444",
  disrupts: "#ef4444",
  challenges: "#ef4444",
  supplies_to: "#3b82f6",
  sourced_from: "#3b82f6",
  distributes: "#3b82f6",
  invested_in: "#f59e0b",
  controls: "#f59e0b",
  correlated_with: "#f59e0b",
  mentions: "#9ca3af",
};

export function getEntityColor(type: string): string {
  return ENTITY_TYPE_COLORS[type] || "#6b7280";
}

export function getPredicateColor(predicate: string): string {
  return PREDICATE_COLORS[predicate] || "#6b7280";
}