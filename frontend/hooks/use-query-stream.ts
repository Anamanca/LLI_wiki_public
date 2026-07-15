"use client";

import { useState, useCallback, useRef } from "react";
import type { Citation, SourceUsage } from "@/types";

interface StreamState {
  answer: string;
  citations: Citation[];
  sourcesUsed: SourceUsage[];
  loading: boolean;
  error: string | null;
}

export function useQueryStream() {
  const [state, setState] = useState<StreamState>({
    answer: "",
    citations: [],
    sourcesUsed: [],
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
    }) => {
      if (abortRef.current) {
        abortRef.current.abort();
      }
      const controller = new AbortController();
      const clientTimeout = setTimeout(() => controller.abort(), 240_000);
      abortRef.current = controller;

      setState({ answer: "", citations: [], sourcesUsed: [], loading: true, error: null });

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
        let answer = "";

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
                }));
                return;
              }
              if (payload.type === "token") {
                answer += payload.content;
                setState((prev) => ({ ...prev, answer }));
              } else if (payload.type === "complete") {
                setState((prev) => ({
                  ...prev,
                  citations: payload.citations || [],
                  sourcesUsed: payload.sources_used || [],
                  loading: false,
                  answer,
                }));
              }
            } catch {
              // skip non-JSON SSE lines
            }
          }
        }
        setState((prev) => ({ ...prev, loading: false }));
      } catch (err: any) {
        if (err.name === "AbortError") return;
        setState((prev) => ({
          ...prev,
          error: err.message || "Stream failed",
          loading: false,
        }));
      } finally {
        clearTimeout(clientTimeout);
      }
    },
    [],
  );

  return { ...state, askQuestion };
}
