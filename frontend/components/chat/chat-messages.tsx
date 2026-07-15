"use client";

import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/types";
import { User, Bot, ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";

interface ChatMessagesProps {
  messages: ChatMessage[];
  isLoading: boolean;
}

export function ChatMessages({ messages, isLoading }: ChatMessagesProps) {
  return (
    <div className="flex-1 overflow-y-auto space-y-4 p-4 min-h-0">
      {messages.length === 0 && !isLoading && (
        <div className="flex items-center justify-center h-full text-muted-foreground">
          <div className="text-center">
            <Bot className="h-12 w-12 mx-auto mb-3 opacity-50" />
            <p>Ask a question about your knowledge base.</p>
          </div>
        </div>
      )}

      {messages.map((msg) => (
        <div
          key={msg.id}
          className={cn(
            "flex gap-3",
            msg.role === "user" ? "justify-end" : "justify-start"
          )}
        >
          {msg.role === "assistant" && (
            <div className="flex-shrink-0 mt-1">
              <Bot className="h-6 w-6 text-primary" />
            </div>
          )}

          <div
            className={cn(
              "max-w-[85%] rounded-lg px-4 py-3",
              msg.role === "user"
                ? "bg-primary text-primary-foreground"
                : "bg-muted"
            )}
          >
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <p className="whitespace-pre-wrap text-sm">{msg.content}</p>
            </div>

            {msg.citations && msg.citations.length > 0 && (
              <div className="mt-2 pt-2 border-t border-border">
                <p className="text-xs font-medium mb-1 text-muted-foreground">
                  Sources:
                </p>
                <div className="flex flex-wrap gap-1">
                  {msg.citations.map((citation, i) => {
                    const hasValidSlug =
                      citation.page_slug &&
                      citation.page_slug.length > 0 &&
                      citation.page_slug !== "kg";

                    if (hasValidSlug) {
                      return (
                        <a
                          key={i}
                          href={`/wiki/${citation.page_slug}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                        >
                          <ExternalLink className="h-3 w-3" />
                          {citation.page_title || citation.section || citation.source_name}
                        </a>
                      );
                    }

                    return (
                      <Badge key={i} variant="secondary" className="text-xs">
                        {citation.page_title || citation.section || citation.source_name || "unknown"}
                      </Badge>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {msg.role === "user" && (
            <div className="flex-shrink-0 mt-1">
              <User className="h-6 w-6" />
            </div>
          )}
        </div>
      ))}

      {isLoading && (
        <div className="flex gap-3 justify-start">
          <Bot className="h-6 w-6 text-primary mt-1" />
          <div className="bg-muted rounded-lg px-4 py-3">
            <div className="flex gap-1">
              <span className="h-2 w-2 rounded-full bg-primary animate-bounce [animation-delay:0ms]" />
              <span className="h-2 w-2 rounded-full bg-primary animate-bounce [animation-delay:150ms]" />
              <span className="h-2 w-2 rounded-full bg-primary animate-bounce [animation-delay:300ms]" />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
