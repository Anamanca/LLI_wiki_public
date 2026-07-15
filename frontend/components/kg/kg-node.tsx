"use client";

import { Handle, Position } from "@xyflow/react";
import { getEntityColor } from "@/lib/kg-colors";

function getNodeSize(eventCount: number): number {
  return Math.min(60, Math.max(30, 20 + eventCount * 2));
}

export function KgNode({ data }: { data: { label: string; type: string; ticker: string | null; eventCount: number } }) {
  const color = getEntityColor(data.type);
  const size = getNodeSize(data.eventCount);

  return (
    <div className="relative flex flex-col items-center">
      <Handle type="target" position={Position.Top} className="!bg-muted-foreground" />
      {data.ticker && (
        <span className="absolute -top-6 left-1/2 -translate-x-1/2 rounded-full px-1.5 py-0.5 text-[10px] font-bold text-white bg-black/60 dark:bg-white/20 whitespace-nowrap">
          {data.ticker}
        </span>
      )}
      <div
        className="flex items-center justify-center rounded-full border-2 border-white dark:border-gray-800 shadow-lg transition-transform hover:scale-110 cursor-pointer"
        style={{ width: size, height: size, backgroundColor: color }}
      >
        <span className="text-[10px] font-bold text-white drop-shadow-sm truncate px-1">
          {data.label}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-muted-foreground" />
    </div>
  );
}