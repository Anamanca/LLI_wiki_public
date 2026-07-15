"use client";

import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ClusterGraph } from "@/components/kg/kg-cluster-graph";
import { KgNodeDetail } from "@/components/kg/kg-node-detail";
import { fetchClusterExpand } from "@/lib/api-client";
import { Network } from "lucide-react";
import type { EntityGraphData } from "@/types";

export default function KnowledgeGraphPage() {
  const [expandedType, setExpandedType] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const { data: expandedData, isLoading: expanding } = useQuery({
    queryKey: ["cluster-expand", expandedType],
    queryFn: () => fetchClusterExpand(expandedType!, 1000),
    enabled: !!expandedType,
  });

  const handleClusterClick = useCallback((nodeType: string) => {
    setExpandedType(nodeType);
  }, []);

  const handleBack = useCallback(() => {
    setExpandedType(null);
    setSelectedNode(null);
  }, []);

  const handleNodeClick = useCallback((nodeId: string) => {
    setSelectedNode((prev) => (prev === nodeId ? null : nodeId));
  }, []);

  const title = expandedType
    ? `Knowledge Graph — ${expandedType.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}`
    : "Knowledge Graph — Cụm thực thể";

  return (
    <div className="flex h-[calc(100vh-4rem)]">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 px-4 py-3 border-b">
          <Network className="h-5 w-5 text-primary" />
          <h1 className="text-lg font-bold">{title}</h1>
          <span className="text-xs text-muted-foreground ml-auto">
            {expandedType
              ? "Click node để xem chi tiết"
              : "Kéo xoay 3D · Scroll zoom · Click cụm để mở rộng"}
          </span>
        </div>
        <div className="h-[calc(100%-49px)]">
          {expanding ? (
            <div className="flex items-center justify-center h-full">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
            </div>
          ) : (
            <ClusterGraph
              onNodeClick={expandedType ? handleNodeClick : handleClusterClick}
              expandedType={expandedType}
              expandedData={expandedData ?? null}
              onBack={handleBack}
            />
          )}
        </div>
      </div>

      {selectedNode && expandedType && (
        <div className="w-64 shrink-0 border-l p-3 overflow-y-auto">
          <KgNodeDetail
            nodeId={selectedNode}
            onClose={() => setSelectedNode(null)}
          />
        </div>
      )}
    </div>
  );
}