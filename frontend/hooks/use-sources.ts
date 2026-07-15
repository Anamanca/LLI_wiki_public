"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchSources,
  fetchSource,
  createSource,
  updateSource,
  scanSource,
  fetchSourceItems,
  skipSourceItem,
  retrySourceItem,
  submitManualTranscript,
} from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import type { CreateSourcePayload, UpdateSourcePayload, ManualTranscriptPayload } from "@/types";

export function useSources(platform?: string, status?: string) {
  return useQuery({
    queryKey: [...queryKeys.sources.all, platform, status],
    queryFn: () => fetchSources(platform, status),
    staleTime: 30_000,
  });
}

export function useSource(id: string) {
  return useQuery({
    queryKey: queryKeys.sources.detail(id),
    queryFn: () => fetchSource(id),
    enabled: !!id,
  });
}

export function useCreateSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateSourcePayload) => createSource(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.sources.all });
    },
  });
}

export function useUpdateSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...payload }: { id: string } & UpdateSourcePayload) =>
      updateSource(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.sources.all });
    },
  });
}

export function useScanSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => scanSource(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: queryKeys.sources.detail(id) });
      qc.invalidateQueries({ queryKey: queryKeys.items(id) });
      qc.invalidateQueries({ queryKey: queryKeys.progress.all });
    },
  });
}

export function useSourceItems(sourceId: string) {
  return useQuery({
    queryKey: queryKeys.items(sourceId),
    queryFn: () => fetchSourceItems(sourceId),
    enabled: !!sourceId,
    staleTime: 15_000,
  });
}

export function useSkipSourceItem(sourceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => skipSourceItem(itemId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.items(sourceId) });
      qc.invalidateQueries({ queryKey: queryKeys.sources.detail(sourceId) });
    },
  });
}

export function useRetrySourceItem(sourceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (itemId: string) => retrySourceItem(itemId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.items(sourceId) });
      qc.invalidateQueries({ queryKey: queryKeys.sources.detail(sourceId) });
    },
  });
}

export function useSubmitTranscript(sourceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, payload }: { itemId: string; payload: ManualTranscriptPayload }) =>
      submitManualTranscript(itemId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.items(sourceId) });
      qc.invalidateQueries({ queryKey: queryKeys.sources.detail(sourceId) });
    },
  });
}
