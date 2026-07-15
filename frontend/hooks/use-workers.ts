"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchWorkers } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export function useWorkers() {
  return useQuery({
    queryKey: queryKeys.workers.all,
    queryFn: fetchWorkers,
    refetchInterval: 10_000,
  });
}
