"use client";

import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/ui/card";
import { fetchEntityGraph } from "@/lib/api-client";

interface KgNodeDetailProps {
  nodeId: string | null;
  onClose: () => void;
}

export function KgNodeDetail({ nodeId, onClose }: KgNodeDetailProps) {
  const { data, isLoading } = useQuery({
    queryKey: ["entity-graph", nodeId],
    queryFn: () => fetchEntityGraph({ entity_id: nodeId!, depth: 1 }),
    enabled: !!nodeId,
  });

  const node = data?.nodes.find((n) => n.id === nodeId);
  if (!nodeId) return null;

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Chi tiết</h3>
        <button
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground text-xs"
        >
          Đóng
        </button>
      </div>

      {isLoading ? (
        <div className="animate-pulse space-y-2">
          <div className="h-4 bg-muted rounded w-3/4" />
          <div className="h-3 bg-muted rounded w-1/2" />
        </div>
      ) : node ? (
        <>
          <div>
            <p className="text-lg font-medium">{node.label}</p>
            <div className="flex items-center gap-2 mt-1">
              <span className="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary">
                {node.type}
              </span>
              {node.ticker && (
                <span className="text-xs font-mono text-muted-foreground">{node.ticker}</span>
              )}
            </div>
          </div>

          <div className="text-xs text-muted-foreground space-y-1">
            <p>Số sự kiện liên quan: <span className="font-medium text-foreground">{node.event_count}</span></p>
          </div>

          {data?.edges && data.edges.length > 0 && (
            <div>
              <p className="text-xs font-medium mb-1.5">Quan hệ ({data.edges.length}):</p>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {data.edges.map((edge, i) => {
                  const otherId = edge.source === nodeId ? edge.target : edge.source;
                  const otherNode = data.nodes.find((n) => n.id === otherId);
                  return (
                    <div key={i} className="flex items-center gap-1.5 text-xs">
                      <span
                        className={`w-2 h-2 rounded-full ${
                          edge.edge_type === "entity_relation" ? "bg-green-500" : "bg-gray-400"
                        }`}
                      />
                      <span className="text-muted-foreground">
                        {edge.predicate.replace(/_/g, " ")}
                      </span>
                      <span className="font-medium">{otherNode?.label || otherId.slice(0, 8)}</span>
                      {edge.confidence !== null && (
                        <span className="text-[10px] text-muted-foreground/60">
                          ({(edge.confidence * 100).toFixed(0)}%)
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">Không tìm thấy thực thể</p>
      )}
    </Card>
  );
}