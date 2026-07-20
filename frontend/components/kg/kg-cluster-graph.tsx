"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { fetchEntityGraph } from "@/lib/api-client";
import { getEntityColor, getPredicateColor } from "@/lib/kg-colors";
import type { EntityGraphData } from "@/types";

const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), { ssr: false });

interface ClusterNode {
  id: string;
  label: string;
  entity_count: number;
  color: string;
}

interface ClusterEdge {
  source: string;
  target: string;
  predicate: string;
  relation_count: number;
}

interface ClusterData {
  clusters: ClusterNode[];
  edges: ClusterEdge[];
}

interface GraphNode3D {
  id: string;
  label: string;
  displayLabel: string;
  color: string;
  val: number;
  type: string;
  ticker: string | null;
  eventCount: number;
}

interface GraphLink3D {
  source: string;
  target: string;
  predicate: string;
  confidence: number | null;
  color: string;
}

interface ClusterGraphProps {
  onNodeClick?: (nodeType: string) => void;
  expandedType: string | null;
  expandedData: EntityGraphData | null;
  onBack: () => void;
}

function entityToGraphNode(n: EntityGraphData["nodes"][0]): GraphNode3D {
  return {
    id: n.id,
    label: n.label,
    displayLabel: n.ticker || n.label,
    color: getEntityColor(n.type),
    val: Math.max(1, Math.min(8, 1 + Math.log2(n.event_count + 1))),
    type: n.type,
    ticker: n.ticker,
    eventCount: n.event_count,
  };
}

function toEntityGraph(data: EntityGraphData): { nodes: GraphNode3D[]; links: GraphLink3D[] } {
  const nodes = data.nodes.map(entityToGraphNode);
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

function toClusterNodes(clusters: ClusterNode[]) {
  return clusters.map((c) => ({
    id: `cluster_${c.id}`,
    label: c.label,
    displayLabel: `${c.label} (${c.entity_count})`,
    color: c.color,
    val: Math.max(3, Math.min(20, 3 + Math.log2(c.entity_count + 1) * 3)),
    type: c.id,
    ticker: null as string | null,
    eventCount: c.entity_count,
  }));
}

function toClusterLinks(edges: ClusterEdge[], clusters: ClusterNode[]) {
  const clusterIds = new Set(clusters.map((c) => c.id));
  return edges
    .filter((e) => clusterIds.has(e.source) && clusterIds.has(e.target))
    .map((e) => ({
      source: `cluster_${e.source}`,
      target: `cluster_${e.target}`,
      predicate: e.predicate,
      confidence: null as number | null,
      color: "rgba(255,255,255,0.3)",
    }));
}

export function ClusterGraph({ onNodeClick, expandedType, expandedData, onBack }: ClusterGraphProps) {
  const [clusterData, setClusterData] = useState<ClusterData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forceGraphReady, setForceGraphReady] = useState(false);
  const fgRef = useRef<any>(null);

  useEffect(() => {
    let mounted = true;
    import("react-force-graph-3d").then(() => {
      if (mounted) setForceGraphReady(true);
    });
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    if (expandedType) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch("/api/cluster-graph");
        if (!res.ok) {
          throw new Error(`${res.status}: ${await res.text().catch(() => "Unknown error")}`);
        }
        const data = await res.json() as ClusterData;
        if (!Array.isArray(data.clusters)) {
          throw new Error("Invalid response shape from /api/cluster-graph");
        }
        if (!cancelled) setClusterData(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Không thể tải cluster graph.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [expandedType]);

  const graphData = useMemo(() => {
    if (expandedType && expandedData) {
      return toEntityGraph(expandedData);
    }
    if (clusterData) {
      return {
        nodes: toClusterNodes(clusterData.clusters),
        links: toClusterLinks(clusterData.edges, clusterData.clusters),
      };
    }
    return null;
  }, [clusterData, expandedType, expandedData]);

  const handleNodeClick = useCallback(
    (node: any) => {
      if (expandedType) return;
      const clusterId = (node.id as string).replace("cluster_", "");
      onNodeClick?.(clusterId);
    },
    [onNodeClick, expandedType]
  );

  if (loading && !expandedType) {
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

  if (!graphData) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
        Đang tải dữ liệu đồ thị...
      </div>
    );
  }

  if (!forceGraphReady) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted-foreground">
        Đang tải thư viện 3D...
      </div>
    );
  }

  return (
    <div className="h-full w-full relative">
      <ForceGraph3D
        key={expandedType ?? "cluster"}
        graphData={graphData}
        nodeLabel={(node: any) => {
          const n = node as GraphNode3D;
          if (n.type && n.eventCount) {
            return `${n.label} [${n.type}] — ${n.eventCount} events`;
          }
          return n.label;
        }}
        nodeColor={(node: any) => (node as GraphNode3D).color}
        nodeVal={(node: any) => (node as GraphNode3D).val}
        linkColor={(link: any) => (link as GraphLink3D).color}
        linkWidth={(link: any) => Math.max(0.3, Math.log2((link as GraphLink3D).confidence ? 2 : 1) + 0.5)}
        linkDirectionalArrowLength={3}
        linkDirectionalArrowRelPos={1}
        linkLabel={(link: any) => (link as GraphLink3D).predicate.replace(/_/g, " ")}
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
      {expandedType && (
        <button
          onClick={onBack}
          className="absolute top-3 left-3 bg-background border rounded px-3 py-1.5 text-sm shadow hover:bg-accent z-10"
        >
          ← Quay lại toàn cảnh
        </button>
      )}
    </div>
  );
}