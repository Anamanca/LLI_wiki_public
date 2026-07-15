"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchProgress, restartSource, restartItem } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export function useProgress() {
  return useQuery({
    queryKey: queryKeys.progress.all,
    queryFn: fetchProgress,
    refetchInterval: 30_000,
  });
}

export function useRestartSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) => restartSource(sourceId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.progress.all });
      qc.invalidateQueries({ queryKey: queryKeys.sources.all });
    },
  });
}

export function useRestartItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => restartItem(itemId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.progress.all });
      qc.invalidateQueries({ queryKey: queryKeys.sources.all });
    },
  });
}
