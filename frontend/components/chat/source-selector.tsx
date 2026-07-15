"use client";

import { Select, SelectContent, SelectItem, SelectValue } from "@/components/ui/select";

interface SourceSelectorProps {
  sourceId: string;
  onSourceChange: (sourceId: string) => void;
  sources: { id: string; name: string }[];
  disabled?: boolean;
}

export function SourceSelector({
  sourceId,
  onSourceChange,
  sources,
  disabled,
}: SourceSelectorProps) {
  return (
    <div className="flex items-center gap-2 min-w-[220px]">
      <label className="text-sm font-medium text-muted-foreground whitespace-nowrap">
        Source:
      </label>
      <Select
        value={sourceId}
        onValueChange={onSourceChange}
      >
        <SelectValue
          value={sourceId ? sources.find((s) => s.id === sourceId)?.name : undefined}
          placeholder="All Sources"
        />
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
