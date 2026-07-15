"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchPages, fetchPage, searchPages } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";

export function usePages(params?: {
  page?: number;
  per_page?: number;
  source_id?: string;
  search?: string;
  sort_by?: string;
  sort_order?: string;
}) {
  return useQuery({
    queryKey: queryKeys.pages.filtered(params),
    queryFn: () => fetchPages(params),
    staleTime: 30_000,
  });
}

export function usePage(slug: string) {
  return useQuery({
    queryKey: queryKeys.pages.detail(slug),
    queryFn: () => fetchPage(slug),
    enabled: !!slug,
  });
}

export function useSearch(query: string) {
  return useQuery({
    queryKey: queryKeys.pages.search(query),
    queryFn: () => searchPages(query),
    enabled: query.length >= 2,
    staleTime: 10_000,
  });
}
