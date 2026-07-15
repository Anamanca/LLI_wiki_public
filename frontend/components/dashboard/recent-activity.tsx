"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDate } from "@/lib/utils";
import type { IngestionAlert } from "@/types";

interface RecentActivityProps {
  alerts?: IngestionAlert[];
  isLoading: boolean;
}

function eventBadgeVariant(
  eventType: string
): "success" | "processing" | "pending" | "error" | "rate_limited" | "secondary" {
  switch (eventType) {
    case "completed":
      return "success";
    case "rate_limit_hit":
      return "rate_limited";
    case "error":
      return "error";
    case "retry":
      return "processing";
    default:
      return "secondary";
  }
}

export function RecentActivity({ alerts, isLoading }: RecentActivityProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Activity</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : !alerts || alerts.length === 0 ? (
          <p className="text-sm text-muted-foreground">No recent activity.</p>
        ) : (
          <div className="space-y-3">
            {alerts.map((alert) => (
              <div
                key={alert.id}
                className="flex items-center justify-between rounded-lg border p-3"
              >
                <div className="space-y-1">
                  <p className="text-sm font-medium">{alert.event_type}</p>
                  <p className="text-xs text-muted-foreground">
                    {alert.message || `Item: ${alert.source_item_id}`}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-muted-foreground">
                    {alert.created_at ? formatDate(alert.created_at) : ""}
                  </span>
                  <Badge variant={eventBadgeVariant(alert.event_type)}>
                    {alert.event_type}
                  </Badge>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
