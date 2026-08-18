"use client";

import { Header } from "@/components/layout/header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useProgress, useRestartItem } from "@/hooks/use-progress";
import { useSources, useScanSource } from "@/hooks/use-sources";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/utils";
import { Database, HardDrive, Brain, Activity, RefreshCw, Trash2 } from "lucide-react";
import { clearAlerts } from "@/lib/api-client";
import { ApiKeysPanel } from "@/components/admin/api-keys-panel";
import { CronJobsPanel } from "@/components/admin/cron-jobs-panel";
import { WorkersPanel } from "@/components/admin/workers-panel";
import { useState } from "react";

export default function AdminPage() {
  const { data: progress, isLoading: progressLoading, refetch: refetchProgress } = useProgress();
  const { data: sourcesData } = useSources();
  const restartItem = useRestartItem();
  const scanSource = useScanSource();
  const [clearingAlerts, setClearingAlerts] = useState(false);

  async function handleClearAlerts() {
    if (!confirm("Clear stale error alerts? Only errors for recovered items will be removed. Active errors remain for debugging.")) return;
    setClearingAlerts(true);
    try {
      await clearAlerts();
      await refetchProgress();
    } catch (e) {
      console.error("Failed to clear alerts:", e);
    } finally {
      setClearingAlerts(false);
    }
  }

  return (
    <div className="space-y-6">
      <Header title="Admin" description="System health and ingestion logs" />

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Database</CardTitle>
            <Database className="h-4 w-4 text-green-500" />
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold text-green-500">Connected</div>
            <p className="text-xs text-muted-foreground">PostgreSQL + pgvector</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Ingestion</CardTitle>
            <Activity className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold">
              {progressLoading ? <Skeleton className="h-6 w-12" /> : progress?.global.processing ?? 0}
            </div>
            <p className="text-xs text-muted-foreground">Jobs processing</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Sources</CardTitle>
            <HardDrive className="h-4 w-4 text-purple-500" />
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold">{sourcesData?.total ?? 0}</div>
            <p className="text-xs text-muted-foreground">Active channels</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">AI Models</CardTitle>
            <Brain className="h-4 w-4 text-orange-500" />
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold text-green-500">Ready</div>
            <p className="text-xs text-muted-foreground">BGE-M3 + Reranker</p>
          </CardContent>
        </Card>
      </div>

      {sourcesData && sourcesData.sources.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Quick Scan</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {sourcesData.sources.slice(0, 5).map((s) => (
                <Button
                  key={s.id}
                  variant="outline"
                  size="sm"
                  onClick={() => scanSource.mutate(s.id)}
                  disabled={scanSource.isPending}
                >
                  <RefreshCw className="mr-1 h-3 w-3" />
                  Scan {s.name}
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <ApiKeysPanel />

      <WorkersPanel />

      <CronJobsPanel />

      <Card id="alerts">
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Recent Alerts</CardTitle>
          {progress?.alerts && progress.alerts.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleClearAlerts}
              disabled={clearingAlerts}
            >
              <Trash2 className="mr-1 h-3 w-3" />
              {clearingAlerts ? "Clearing..." : "Clear Stale"}
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {progressLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : !progress?.alerts?.length ? (
            <p className="text-sm text-muted-foreground">No recent alerts.</p>
          ) : (
            <div className="space-y-3">
              {progress.alerts.map((alert) => (
                <div
                  key={alert.id}
                  className="flex items-center justify-between rounded-lg border p-3"
                >
                  <div className="space-y-1">
                    <p className="text-sm font-medium">{alert.message || alert.event_type}</p>
                    <p className="text-xs text-muted-foreground">
                      Item: {alert.source_item_id ?? "N/A"}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-muted-foreground">
                      {alert.created_at ? formatDate(alert.created_at) : ""}
                    </span>
                    <Badge variant={
                      alert.event_type === "error" || alert.event_type === "api_key_error"
                        ? "error"
                        : alert.event_type === "retry"
                        ? "processing"
                        : alert.event_type === "no_captions"
                        ? "warning"
                        : alert.event_type === "extract_done" || alert.event_type === "wiki_done"
                        ? "success"
                        : "rate_limited"
                    }>
                      {alert.event_type}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
