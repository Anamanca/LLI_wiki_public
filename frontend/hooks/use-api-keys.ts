"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchApiKeys,
  createApiKey,
  updateApiKey,
  deleteApiKey,
  activateApiKey,
} from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import type { CreateApiKeyPayload, UpdateApiKeyPayload } from "@/types";

export function useApiKeys() {
  return useQuery({
    queryKey: queryKeys.apiKeys.all,
    queryFn: fetchApiKeys,
    refetchInterval: 10_000,
  });
}

export function useCreateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateApiKeyPayload) => createApiKey(payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys.all });
    },
  });
}

export function useUpdateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: UpdateApiKeyPayload }) =>
      updateApiKey(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys.all });
    },
  });
}

export function useDeleteApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteApiKey(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys.all });
    },
    onError: (err: Error) => {
      // 409 = cannot delete the last active key; surface a readable message.
      console.error("Failed to delete API key:", err);
    },
  });
}

export function useActivateApiKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => activateApiKey(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.apiKeys.all });
    },
  });
}