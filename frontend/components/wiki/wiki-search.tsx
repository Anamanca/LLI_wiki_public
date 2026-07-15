"use client";

import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectValue } from "@/components/ui/select";
import { Search } from "lucide-react";

interface WikiSearchProps {
  query: string;
  onQueryChange: (query: string) => void;
  sourceId: string;
  onSourceChange: (sourceId: string) => void;
  sources: { id: string; name: string }[];
}

export function WikiSearch({
  query,
  onQueryChange,
  sourceId,
  onSourceChange,
  sources,
}: WikiSearchProps) {
  return (
    <div className="flex flex-col sm:flex-row gap-3 mb-6">
      <div className="relative flex-1">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          placeholder="Search wiki pages..."
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          className="pl-9"
        />
      </div>
      <Select
        value={sourceId}
        displayValue={sourceId ? sources.find((s) => s.id === sourceId)?.name : undefined}
        onValueChange={onSourceChange}
      >
        <SelectValue placeholder="All Sources" />
        <SelectContent onSelect={onSourceChange}>
          <SelectItem value="">All Sources</SelectItem>
          {sources.map((s) => (
            <SelectItem key={s.id} value={s.id}>
              {s.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
