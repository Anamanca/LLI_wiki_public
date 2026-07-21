"use client";

import { useState, useCallback, useRef } from "react";
import type { Citation, SourceUsage } from "@/types";

type QueryStatus = "processing" | "retrieving" | "thinking" | "summarizing" | null;

interface StreamState {
  answer: string;
  citations: Citation[];
  sourcesUsed: SourceUsage[];
  status: QueryStatus;
  loading: boolean;
  error: string | null;
}

const STATUS_LABELS: Record<Exclude<QueryStatus, null>, string> = {
  processing: "Đang phân tích câu hỏi...",
  retrieving: "Đang tìm kiếm tài liệu...",
  thinking: "Đang suy luận...",
  summarizing: "Đang tổng hợp câu trả lờI...",
};

export function useQueryStream() {
  const [state, setState] = useState<StreamState>({
    answer: "",
    citations: [],
    sourcesUsed: [],
    status: null,
    loading: false,
    error: null,
  });
  const abortRef = useRef<AbortController | null>(null);

  const askQuestion = useCallback(
    async (body: {
      question: string;
      session_id?: string;
      history?: { role: string; content: string }[];
      source_id?: string;
      top_k?: number;
      from_date?: string;
      to_date?: string;
    }) => {
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const controller = new AbortController();
      const clientTimeout = setTimeout(() => controller.abort(), 180_000);
      abortRef.current = controller;

      setState({ answer: "", citations: [], sourcesUsed: [], status: "processing", loading: true, error: null });

      try {
        const apiBase = process.env.NEXT_PUBLIC_API_URL || "/api";
        const response = await fetch(`${apiBase}/query/stream`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...body, top_k: body.top_k ?? 10 }),
          signal: controller.signal,
        });

        if (!response.ok) {
          throw new Error(`Server error: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error("No response body");

        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const data = line.slice(6);
            if (data === "[DONE]") break;
            try {
              const payload = JSON.parse(data);
              if (payload.error) {
                setState((prev) => ({
                  ...prev,
                  error: payload.error,
                  loading: false,
                  status: null,
                }));
                return;
              }
              if (payload.type === "status") {
                const status = payload.status as QueryStatus;
                if (status && status in STATUS_LABELS) {
                  setState((prev) => ({ ...prev, status }));
                }
              } else if (payload.type === "complete") {
                setState((prev) => ({
                  ...prev,
                  answer: payload.answer || "",
                  citations: payload.citations || [],
                  sourcesUsed: payload.sources_used || [],
                  loading: false,
                  status: null,
                }));
              }
            } catch {
              // skip non-JSON SSE lines
            }
          }
        }
        setState((prev) => ({ ...prev, loading: false, status: null }));
      } catch (err: any) {
        if (err.name === "AbortError") return;
        setState((prev) => ({
          ...prev,
          error: err.message || "Stream failed",
          loading: false,
          status: null,
        }));
      } finally {
        clearTimeout(clientTimeout);
      }
    },
    [],
  );

  return { ...state, askQuestion, statusLabel: state.status ? STATUS_LABELS[state.status] : "" };
}
