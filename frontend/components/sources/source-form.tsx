"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectValue } from "@/components/ui/select";
import type { CreateSourcePayload } from "@/types";

interface SourceFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (payload: CreateSourcePayload) => void;
  isSubmitting: boolean;
}

const PLATFORMS = [
  { value: "youtube", label: "YouTube" },
  { value: "facebook", label: "Facebook" },
  { value: "blog", label: "Blog" },
  { value: "other", label: "Other" },
];

export function SourceForm({ open, onOpenChange, onSubmit, isSubmitting }: SourceFormProps) {
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("youtube");
  const [externalId, setExternalId] = useState("");
  const [url, setUrl] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit({ name, platform, external_id: externalId, url });
    setName("");
    setExternalId("");
    setUrl("");
    setPlatform("youtube");
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent onClose={() => onOpenChange(false)}>
        <DialogHeader>
          <DialogTitle>Add Source</DialogTitle>
          <DialogDescription>
            Add a new YouTube channel or other content source to monitor.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 mt-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Name</label>
            <Input
              placeholder="Channel name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Platform</label>
            <Select value={platform} onValueChange={setPlatform}>
              <SelectValue value={PLATFORMS.find((p) => p.value === platform)?.label} />
              <SelectContent onSelect={setPlatform}>
                {PLATFORMS.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">External ID</label>
            <Input
              placeholder="YouTube channel ID (@handle)"
              value={externalId}
              onChange={(e) => setExternalId(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">URL</label>
            <Input
              placeholder="https://youtube.com/@channel"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
            />
          </div>
          <Button type="submit" className="w-full" disabled={isSubmitting}>
            {isSubmitting ? "Adding..." : "Add Source"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
