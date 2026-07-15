"use client";

import { useMutation } from "@tanstack/react-query";
import { postQuery } from "@/lib/api-client";
import type { QueryRequest, QueryResponse } from "@/types";

export function useQueryMutation() {
  return useMutation<QueryResponse, Error, QueryRequest>({
    mutationFn: (payload: QueryRequest) => postQuery(payload),
  });
}
