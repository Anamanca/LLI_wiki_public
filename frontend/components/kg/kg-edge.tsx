"use client";

import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from "@xyflow/react";
import { getPredicateColor } from "@/lib/kg-colors";

export function KgEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const predicate = (data as { predicate?: string })?.predicate || "";
  const confidence = (data as { confidence?: number })?.confidence;
  const color = getPredicateColor(predicate);
  const isLowConf = confidence !== undefined && confidence !== null && confidence < 0.7;

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: color,
          strokeWidth: 2,
          strokeDasharray: isLowConf ? "5,5" : undefined,
          opacity: isLowConf ? 0.6 : 1,
        }}
        markerEnd={markerEnd}
      />
      {predicate && (
        <EdgeLabelRenderer>
          <div
            className="absolute rounded-full bg-background border px-2 py-0.5 text-[10px] font-medium shadow-sm pointer-events-none whitespace-nowrap"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            }}
          >
            {predicate.replace(/_/g, " ")}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}