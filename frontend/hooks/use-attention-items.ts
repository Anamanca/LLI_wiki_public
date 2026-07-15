"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchAttentionItems } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export function useAttentionItems(params?: {
  page?: number;
  per_page?: number;
}) {
  return useQuery({
    queryKey: queryKeys.attentionItems.filtered(params),
    queryFn: () => fetchAttentionItems(params),
    staleTime: 15_000,
  });
}
