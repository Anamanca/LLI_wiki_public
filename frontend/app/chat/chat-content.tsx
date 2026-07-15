"use client";

import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { Header } from "@/components/layout/header";
import { ChatMessages } from "@/components/chat/chat-messages";
import { ChatInput } from "@/components/chat/chat-input";
import { SourceSelector } from "@/components/chat/source-selector";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useSources } from "@/hooks/use-sources";
import { useQueryStream } from "@/hooks/use-query-stream";
import { createChatSession, fetchChatSession, saveChatSession } from "@/lib/api-client";
import { Plus, Loader2 } from "lucide-react";
import type { ChatMessage } from "@/types";

const SESSION_KEY = "llm-wiki-chat-session";
const POLL_INTERVAL = 5000;

function mapMessages(raw: { role: string; content: string }[]): ChatMessage[] {
  return raw.map((m, i) => ({
    role: m.role as "user" | "assistant",
    content: m.content,
    id: `${m.role}-${i}-${Date.now()}`,
    timestamp: new Date().toISOString(),
  }));
}

export function ChatContent() {
  const searchParams = useSearchParams();
  const urlSessionId = searchParams.get("session") || "";

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [loading, setLoading] = useState(true);

  const activeRef = useRef("");
  const savingGate = useRef(false);
  const pollingRef = useRef(false);
  const pendingMsgId = useRef<string | null>(null);
  const wasStreaming = useRef(false);

  const { data: sourcesData } = useSources();
  const streamQuery = useQueryStream();

  const isStreaming = streamQuery.loading;

  const sourceList = useMemo(
    () => sourcesData?.sources?.map((s) => ({ id: s.id, name: s.name })) || [],
    [sourcesData],
  );

  const switchTo = useCallback(async (id: string) => {
    if (id === activeRef.current && activeRef.current !== "") return;
    savingGate.current = true;
    setLoading(true);
    setMessages([]);
    setSessionId("");

    try {
      const session = await fetchChatSession(id);
      activeRef.current = session.id;
      setSessionId(session.id);
      setMessages(mapMessages(session.messages));
      localStorage.setItem(SESSION_KEY, session.id);
    } catch {
      const session = await createChatSession();
      activeRef.current = session.id;
      setSessionId(session.id);
      setMessages([]);
      localStorage.setItem(SESSION_KEY, session.id);
    }
    setLoading(false);
    savingGate.current = false;
  }, []);

  useEffect(() => {
    if (urlSessionId) {
      switchTo(urlSessionId);
    } else {
      const saved = localStorage.getItem(SESSION_KEY);
      if (saved) {
        switchTo(saved);
      } else {
        createChatSession().then((s) => {
          activeRef.current = s.id;
          setSessionId(s.id);
          setMessages([]);
          localStorage.setItem(SESSION_KEY, s.id);
          setLoading(false);
        });
      }
    }
  }, [urlSessionId, switchTo]);

  useEffect(() => {
    if (!sessionId || loading) return;
    const poll = async () => {
      if (pollingRef.current) return;
      pollingRef.current = true;
      try {
        const session = await fetchChatSession(sessionId);
        setMessages((prev) => {
          if (session.messages.length <= prev.length) return prev;
          const fresh = session.messages.slice(prev.length);
          const appended = fresh.map((m, i) => ({
            role: m.role as "user" | "assistant",
            content: m.content,
            id: `telegram-${Date.now()}-${i}`,
            timestamp: new Date().toISOString(),
          }));
          return [...prev, ...appended];
        });
      } catch {}
      finally { pollingRef.current = false; }
    };
    const interval = setInterval(poll, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [sessionId, loading]);

  // Update streaming message in real-time
  useEffect(() => {
    if (!pendingMsgId.current) return;
    setMessages((prev) =>
      prev.map((m) =>
        m.id === pendingMsgId.current
          ? {
              ...m,
              content: streamQuery.answer,
              citations: streamQuery.citations.length > 0 ? streamQuery.citations : m.citations,
            }
          : m,
      ),
    );
  }, [streamQuery.answer, streamQuery.citations]);

  // Handle streaming complete / error — runs once when isStreaming transitions true → false
  useEffect(() => {
    if (isStreaming) {
      wasStreaming.current = true;
      return;
    }
    if (!wasStreaming.current) return;
    wasStreaming.current = false;

    const finalId = pendingMsgId.current;
    pendingMsgId.current = null;

    setMessages((prev) => {
      const next = prev.map((m) => {
        if (m.id !== finalId) return m;
        if (streamQuery.error) return { ...m, content: `Error: ${streamQuery.error}` };
        if (streamQuery.citations.length > 0) return { ...m, citations: streamQuery.citations };
        return m;
      });
      if (sessionId) {
        saveChatSession(sessionId, next.map((m) => ({ role: m.role, content: m.content })))
          .catch((e) => console.warn("saveChatSession failed:", e));
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming]);

  async function handleNewChat() {
    savingGate.current = true;
    setLoading(true);
    setMessages([]);
    setSessionId("");

    const session = await createChatSession();
    activeRef.current = session.id;
    setSessionId(session.id);
    setMessages([]);
    localStorage.setItem(SESSION_KEY, session.id);

    savingGate.current = false;
    setLoading(false);
    window.dispatchEvent(new Event("chat-session-changed"));
  }

  const handleSend = useCallback(
    (question: string) => {
      const userMsgId = `user-${Date.now()}`;
      const assistantMsgId = `assistant-${Date.now()}`;
      pendingMsgId.current = assistantMsgId;

      setMessages((prev) => [
        ...prev,
        { id: userMsgId, role: "user", content: question, timestamp: new Date().toISOString() },
        { id: assistantMsgId, role: "assistant", content: "", citations: [], timestamp: new Date().toISOString() },
      ]);

      streamQuery.askQuestion({
        question,
        source_id: sourceId || undefined,
        session_id: sessionId || undefined,
        history: messages.slice(-12).map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.content,
        })),
      });
    },
    [sourceId, sessionId, messages, streamQuery],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[calc(100vh-6rem)]">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-3 h-[calc(100vh-2rem)] flex flex-col">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <Header title="Chat" description="Ask questions about your knowledge base" />
        <div className="flex items-center gap-3">
          <SourceSelector sourceId={sourceId} onSourceChange={setSourceId} sources={sourceList} disabled={isStreaming} />
          <Button variant="outline" size="sm" onClick={handleNewChat} className="gap-1.5">
            <Plus className="h-4 w-4" />
            New Chat
          </Button>
        </div>
      </div>
      <Card className="flex-1 flex flex-col overflow-hidden min-h-0">
        <CardContent className="flex-1 flex flex-col p-0 min-h-0">
          <ChatMessages messages={messages} isLoading={isStreaming} />
          <ChatInput onSend={handleSend} disabled={isStreaming} />
        </CardContent>
      </Card>
    </div>
  );
}
