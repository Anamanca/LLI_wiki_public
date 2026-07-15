"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { fetchFullEntityGraph } from "@/lib/api-client";
import { getEntityColor, getPredicateColor } from "@/lib/kg-colors";
import type { EntityGraphData } from "@/types";

const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), { ssr: false });

interface GraphNode3D {
  id: string;
  label: string;
  type: string;
  ticker: string | null;
  eventCount: number;
  color: string;
  val: number;
}

interface GraphLink3D {
  source: string;
  target: string;
  predicate: string;
  confidence: number | null;
  color: string;
}

interface KgGraphProps {
  onNodeClick?: (nodeId: string) => void;
}

function toGraphData(data: EntityGraphData): { nodes: GraphNode3D[]; links: GraphLink3D[] } {
  const nodes: GraphNode3D[] = data.nodes.map((n) => ({
    id: n.id,
    label: n.label,
    type: n.type,
    ticker: n.ticker,
    eventCount: n.event_count,
    color: getEntityColor(n.type),
    val: Math.max(1, Math.min(10, 1 + Math.log2(n.event_count + 1))),
  }));

  const nodeIdSet = new Set(nodes.map((n) => n.id));
  const links: GraphLink3D[] = data.edges
    .filter((e) => e.edge_type === "entity_relation" && nodeIdSet.has(e.source) && nodeIdSet.has(e.target))
    .map((e) => ({
      source: e.source,
      target: e.target,
      predicate: e.predicate,
      confidence: e.confidence,
      color: getPredicateColor(e.predicate),
    }));

  return { nodes, links };
}

export function KgGraph({ onNodeClick }: KgGraphProps) {
  const [graphData, setGraphData] = useState<{ nodes: GraphNode3D[]; links: GraphLink3D[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const fgRef = useRef<any>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchFullEntityGraph();
        if (cancelled) return;
        if (!data.nodes.length) {
          setError("Chưa có dữ liệu quan hệ thực thể. Hãy chạy backfill trước.");
          return;
        }
        const g = toGraphData(data);
        setGraphData(g);
      } catch {
        if (!cancelled) setError("Không thể tải dữ liệu đồ thị.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  const handleNodeClick = useCallback(
    (node: any) => {
      onNodeClick?.(node.id as string);
    },
    [onNodeClick]
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
        {error}
      </div>
    );
  }

  if (!graphData) return null;

  return (
    <div className="h-full w-full">
      <ForceGraph3D
        ref={fgRef}
        graphData={graphData}
        nodeLabel={(node: any) => {
          const n = node as GraphNode3D;
          const parts = [n.label, n.type.replace(/_/g, " ")];
          if (n.ticker) parts.push(`(${n.ticker})`);
          parts.push(`${n.eventCount} events`);
          return parts.join(" — ");
        }}
        nodeColor={(node: any) => (node as GraphNode3D).color}
        nodeVal={(node: any) => (node as GraphNode3D).val}
        linkColor={(link: any) => (link as GraphLink3D).color}
        linkWidth={1}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={1}
        linkLabel={(link: any) => {
          const l = link as GraphLink3D;
          return `${l.predicate.replace(/_/g, " ")}${l.confidence !== null ? ` (${(l.confidence * 100).toFixed(0)}%)` : ""}`;
        }}
        onNodeClick={handleNodeClick}
        backgroundColor="rgba(0,0,0,0)"
        width={typeof window !== "undefined" ? window.innerWidth - 300 : 1200}
        height={typeof window !== "undefined" ? window.innerHeight - 120 : 800}
        showNavInfo={false}
        cooldownTicks={50}
        onEngineStop={() => {
          if (fgRef.current) fgRef.current.zoomToFit(400, 50);
        }}
      />
    </div>
  );
}