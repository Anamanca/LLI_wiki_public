"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Loader2, X } from "lucide-react";

interface AddTranscriptModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (transcriptText: string) => void;
  videoTitle: string | null;
  isPending: boolean;
}

export function AddTranscriptModal({
  isOpen,
  onClose,
  onSubmit,
  videoTitle,
  isPending,
}: AddTranscriptModalProps) {
  const [text, setText] = useState("");

  if (!isOpen) return null;

  const handleSubmit = () => {
    if (!text.trim()) return;
    onSubmit(text.trim());
    setText("");
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="relative z-10 w-full max-w-lg rounded-lg border bg-card p-6 shadow-lg">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">Add Transcript</h2>
          <button
            onClick={onClose}
            className="rounded-sm opacity-70 hover:opacity-100"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {videoTitle && (
          <p className="text-sm text-muted-foreground mb-4">{videoTitle}</p>
        )}

        <Textarea
          placeholder="Paste transcript text here (SRT, VTT, or plain text)"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="min-h-[200px] mb-4"
          disabled={isPending}
        />

        <p className="text-xs text-muted-foreground mb-4">
          Once submitted, the system will classify, embed, and integrate this
          transcript into the wiki.
        </p>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose} disabled={isPending}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isPending || !text.trim()}>
            {isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Processing...
              </>
            ) : (
              "Process"
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
