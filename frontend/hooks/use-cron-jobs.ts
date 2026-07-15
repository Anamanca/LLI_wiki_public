"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchCronJobs, startCronJob, stopCronJob } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export function useCronJobs() {
  return useQuery({
    queryKey: queryKeys.cronJobs.all,
    queryFn: fetchCronJobs,
    refetchInterval: 15_000,
  });
}

export function useStartCronJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => startCronJob(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.cronJobs.all });
    },
  });
}

export function useStopCronJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => stopCronJob(jobId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.cronJobs.all });
    },
  });
}
