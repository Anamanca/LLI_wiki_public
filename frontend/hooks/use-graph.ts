"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchGraph } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export function useGraph(sourceId?: string) {
  return useQuery({
    queryKey: queryKeys.graph.bySource(sourceId),
    queryFn: () => fetchGraph(sourceId),
    enabled: true,
    staleTime: 60_000,
  });
}
