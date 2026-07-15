"use client";

import { useQuery } from "@tanstack/react-query";
import { StatsCards } from "@/components/dashboard/stats-cards";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useProgress } from "@/hooks/use-progress";
import { useAttentionItems } from "@/hooks/use-attention-items";
import { fetchSystemStats } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import type { ProcessingItem } from "@/types";
import { Clock, Loader2, AlertTriangle, Lock, Cpu, HardDrive, MemoryStick } from "lucide-react";

function fmtElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

function CompactHeaderStats() {
  const { data } = useQuery({
    queryKey: queryKeys.systemStats,
    queryFn: fetchSystemStats,
    refetchInterval: 5000,
  });

  if (!data) return null;

  return (
    <div className="flex items-center gap-4 shrink-0 text-xs text-muted-foreground pt-1.5">
      <span className="flex items-center gap-1.5">
        <Cpu className="h-3.5 w-3.5" />
        <span className="font-mono tabular-nums">{data.cpu_percent.toFixed(0)}%</span>
      </span>
      <span className="flex items-center gap-1.5">
        <MemoryStick className="h-3.5 w-3.5" />
        <span className="font-mono tabular-nums">{data.ram_used_gb.toFixed(1)}/{data.ram_total_gb.toFixed(0)} GB</span>
      </span>
      <span className="flex items-center gap-1.5">
        <HardDrive className="h-3.5 w-3.5" />
        <span className="font-mono tabular-nums">{data.disk_used_gb.toFixed(0)}/{data.disk_total_gb.toFixed(0)} GB</span>
      </span>
    </div>
  );
}

export default function DashboardPage() {
  const { data: progress, isLoading } = useProgress();
  const { data: attentionData, isLoading: attentionLoading } = useAttentionItems({
    page: 1,
    per_page: 100,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-muted-foreground">System overview and ingestion progress</p>
        </div>
        <CompactHeaderStats />
      </div>

      <StatsCards progress={progress} isLoading={isLoading} />

      <div className="flex gap-6">
        <div className="flex-1 space-y-6 min-w-0">
          {isLoading ? (
            <Card>
              <CardHeader><CardTitle>Ingestion Progress</CardTitle></CardHeader>
              <CardContent><Skeleton className="h-8 w-full" /></CardContent>
            </Card>
          ) : progress ? (
            <>
              <Card>
                <CardHeader>
                  <CardTitle>Ingestion Progress</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-wrap gap-3">
                    <StatusBadge label="Pending" count={progress.global.pending} variant="pending" />
                    <StatusBadge label="Pending Transcribe" count={progress.global.pending_transcribe} variant="pending" />
                    <StatusBadge label="Waiting for Wiki" count={progress.global.waiting_for_wiki} variant="pending" />
                    <StatusBadge label="Processing" count={progress.global.processing} variant="processing" />
                    <StatusBadge label="Done Today" count={progress.global.done_today} variant="success" />
                    <StatusBadge label="Failed" count={progress.global.failed} variant="error" />
                    <StatusBadge label="Rate Limited" count={progress.global.rate_limited} variant="rate_limited" />
                  </div>
                </CardContent>
              </Card>

              {progress.requires_membership_count > 0 && (
                <div className="flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-2.5 text-sm text-amber-700 dark:text-amber-400">
                  <Lock className="h-4 w-4 shrink-0" />
                  <span>{progress.requires_membership_count.toLocaleString()} member-only videos</span>
                  <span className="hidden sm:inline text-amber-600/70 dark:text-amber-400/60">&mdash; transcripts unavailable, requires channel membership</span>
                </div>
              )}

              {progress.processing_items.length > 0 && (
                <ProcessingJobsCard items={progress.processing_items} />
              )}

              <NeedAttentionCard
                data={attentionData}
                isLoading={attentionLoading}
              />
            </>
          ) : null}

          <RecentActivity alerts={progress?.alerts} isLoading={isLoading} />
        </div>
      </div>
    </div>
  );
}

function ProcessingJobsCard({ items }: { items: ProcessingItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
          Active Processing Jobs ({items.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="max-h-[350px] overflow-y-auto space-y-3 pr-1">
          {items.map((item) => (
            <div
              key={item.id}
              className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate" title={item.title}>
                    {item.title}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {item.source_name} &middot; {item.video_id}
                  </p>
                  <div className="flex items-center gap-2 mt-2">
                    <Badge variant="processing" className="text-xs">
                      {item.stage_label}
                    </Badge>
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {fmtElapsed(item.elapsed_seconds)}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function StatusBadge({
  label,
  count,
  variant,
}: {
  label: string;
  count: number;
  variant: "success" | "processing" | "pending" | "error" | "rate_limited";
}) {
  return (
    <Badge variant={variant} className="text-xs">
      {label}: {count}
    </Badge>
  );
}

function NeedAttentionCard({
  data,
  isLoading,
}: {
  data?: import("@/types").AttentionItemsResponse;
  isLoading: boolean;
}) {
  const total = data?.total ?? 0;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <AlertTriangle className="h-4 w-4 text-orange-500" />
          Need Attention ({total})
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : !data?.items.length ? (
          <p className="text-sm text-muted-foreground">All clear — no items need attention.</p>
        ) : (
          <div className="max-h-[400px] overflow-y-auto space-y-2 pr-1">
            {data.items.map((item) => (
              <div
                key={item.id}
                className="flex items-start justify-between rounded-md border p-2.5 text-sm"
              >
                <div className="min-w-0 flex-1 mr-3">
                  <p className="font-medium truncate" title={item.title ?? item.video_id}>
                    {item.title || item.video_id}
                  </p>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {item.source_name} &middot; {item.video_id}
                  </p>
                  {item.error_message && (
                    <p className="text-xs text-red-500 mt-0.5 truncate">{item.error_message}</p>
                  )}
                </div>
                <Badge
                  variant={
                    item.status === "failed" ? "error" :
                    item.status === "no_captions" ? "outline" :
                    "secondary"
                  }
                  className="text-xs shrink-0"
                >
                  {item.status}
                </Badge>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
