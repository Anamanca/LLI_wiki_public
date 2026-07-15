"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  Radio,
  BookOpen,
  MessageCircle,
  Settings,
  Moon,
  Sun,
  AlertTriangle,
  ChevronDown,
  Trash2,
  Network,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useProgress } from "@/hooks/use-progress";
import { fetchChatSessions, deleteChatSession } from "@/lib/api-client";
import { useEffect, useState } from "react";
import type { ChatSessionMeta } from "@/types";

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/sources", label: "Sources", icon: Radio },
  { href: "/wiki", label: "Wiki", icon: BookOpen },
  { href: "/kg", label: "Graph", icon: Network },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { data: progress } = useProgress();
  const [dark, setDark] = useState(false);
  const [chatOpen, setChatOpen] = useState(true);
  const [sessions, setSessions] = useState<ChatSessionMeta[]>([]);

  useEffect(() => {
    const stored = localStorage.getItem("theme");
    if (stored === "dark" || (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
      setDark(true);
      document.documentElement.classList.add("dark");
    }
  }, []);

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  }

  const refreshSessions = () => {
    fetchChatSessions()
      .then(setSessions)
      .catch(() => {});
  };

  useEffect(() => {
    refreshSessions();
    const handler = () => refreshSessions();
    window.addEventListener("chat-session-changed", handler);
    const interval = setInterval(refreshSessions, 5000);
    return () => {
      window.removeEventListener("chat-session-changed", handler);
      clearInterval(interval);
    };
  }, [pathname]);

  const currentSessionId = typeof window !== "undefined"
    ? localStorage.getItem("llm-wiki-chat-session")
    : null;

  function handleSessionClick(sessionId: string) {
    localStorage.setItem("llm-wiki-chat-session", sessionId);
    window.dispatchEvent(new Event("chat-session-changed"));
    router.push(`/chat?session=${sessionId}`);
  }

  async function handleDelete(sessionId: string, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    await deleteChatSession(sessionId);
    if (currentSessionId === sessionId) {
      localStorage.removeItem("llm-wiki-chat-session");
    }
    refreshSessions();
  }

  const alertCount = progress?.alerts?.length || 0;
  const isChat = pathname === "/chat";

  return (
    <aside className="fixed left-0 top-0 z-40 h-screen w-64 border-r bg-card">
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-2 border-b px-6 py-4">
          <BookOpen className="h-6 w-6 text-primary" />
          <span className="text-lg font-bold">LLM Wiki</span>
        </div>

        <nav className="flex-1 space-y-1 p-4 overflow-y-auto">
          {navItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center justify-between rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                )}
              >
                <span className="flex items-center gap-3">
                  <item.icon className="h-4 w-4" />
                  {item.label}
                </span>
              </Link>
            );
          })}

          {/* Chat nav with session sub-items */}
          <div>
            <button
              onClick={() => {
                if (isChat) {
                  setChatOpen(!chatOpen);
                } else {
                  router.push("/chat");
                  setChatOpen(true);
                }
              }}
              className={cn(
                "flex items-center justify-between w-full rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isChat
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )}
            >
              <span className="flex items-center gap-3">
                <MessageCircle className="h-4 w-4" />
                Chat
              </span>
              <ChevronDown className={cn("h-3 w-3 transition-transform", chatOpen && "rotate-180")} />
            </button>

            {chatOpen && (
              <div className="ml-4 mt-1 space-y-0.5 max-h-[220px] overflow-y-auto">
                {sessions.map((s) => (
                  <div
                    key={s.id}
                    className="group flex items-center justify-between"
                  >
                    <button
                      onClick={() => handleSessionClick(s.id)}
                      className={cn(
                        "flex-1 text-left truncate rounded-md px-3 py-1.5 text-xs transition-colors",
                        currentSessionId === s.id && isChat
                          ? "bg-accent text-accent-foreground font-medium"
                          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
                      )}
                    >
                      {s.title || "Untitled"}
                    </button>
                    <button
                      onClick={(e) => handleDelete(s.id, e)}
                      className="opacity-0 group-hover:opacity-100 p-0.5 text-muted-foreground hover:text-red-500 transition-all rounded"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
                {sessions.length === 0 && (
                  <p className="text-xs text-muted-foreground/50 px-3 py-1">No sessions yet</p>
                )}
              </div>
            )}
          </div>

          {/* Admin */}
          <Link
            href="/admin"
            className={cn(
              "flex items-center justify-between rounded-lg px-3 py-2 text-sm font-medium transition-colors",
              pathname === "/admin"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
            )}
          >
            <span className="flex items-center gap-3">
              <Settings className="h-4 w-4" />
              Admin
            </span>
            {alertCount > 0 && (
              <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-red-500 px-1 text-xs font-bold text-white">
                {alertCount}
              </span>
            )}
          </Link>
        </nav>

        <div className="border-t p-4 space-y-3">
          {progress && (
            <div className="space-y-1 text-xs text-muted-foreground">
              <div className="flex justify-between">
                <span>Sources</span>
                <span className="font-mono">{progress.per_source.length}</span>
              </div>
              <div className="flex justify-between">
                <span>Done Today</span>
                <span className="font-mono text-green-400">{progress.global.done_today}</span>
              </div>
              <div className="flex justify-between">
                <span>Processing</span>
                <span className="font-mono text-blue-400">{progress.global.processing}</span>
              </div>
              {alertCount > 0 && (
                <div className="flex justify-between">
                  <Link href="/admin#alerts" className="flex items-center gap-1 text-red-400 hover:text-red-300 transition-colors">
                    <AlertTriangle className="h-3 w-3" />
                    <span>Alerts</span>
                  </Link>
                  <span className="font-mono text-red-400">{alertCount}</span>
                </div>
              )}
            </div>
          )}

          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            onClick={toggleTheme}
          >
            {dark ? (
              <Sun className="mr-2 h-4 w-4" />
            ) : (
              <Moon className="mr-2 h-4 w-4" />
            )}
            {dark ? "Light Mode" : "Dark Mode"}
          </Button>
        </div>
      </div>
    </aside>
  );
}
