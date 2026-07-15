"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { Header } from "@/components/layout/header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { AddTranscriptModal } from "@/components/sources/add-transcript-modal";
import {
  useSource,
  useScanSource,
  useSourceItems,
  useSkipSourceItem,
  useRetrySourceItem,
  useSubmitTranscript,
} from "@/hooks/use-sources";
import { useRestartSource } from "@/hooks/use-progress";
import {
  ArrowLeft,
  RefreshCw,
  Scan,
  ExternalLink,
  SkipForward,
  RotateCcw,
  FileText,
  Loader2,
  Lock,
} from "lucide-react";
import Link from "next/link";
import type { SourceItem } from "@/types";

export default function SourceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: source, isLoading, error } = useSource(id);
  const { data: itemsData, isLoading: itemsLoading } = useSourceItems(id);
  const scanSource = useScanSource();
  const restartSourceMutation = useRestartSource();
  const skipItem = useSkipSourceItem(id);
  const retryItem = useRetrySourceItem(id);
  const submitTranscript = useSubmitTranscript(id);

  const [transcriptModal, setTranscriptModal] = useState<{
    itemId: string;
    title: string | null;
  } | null>(null);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  if (error || !source) {
    return (
      <div className="space-y-6">
        <Link
          href="/sources"
          className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back to Sources
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load source: {error?.message || "Source not found"}
        </div>
      </div>
    );
  }

  const breakdown = source.status_breakdown || {};
  const allItems = itemsData?.items || [];
  const memberOnlyCount = allItems.filter((i) => i.status === "requires_membership").length;
  const needsAttention = allItems.filter(
    (i) => i.status !== "requires_membership" && i.status !== "completed"
  );
  const needsAttentionTotal = needsAttention.length;

  const statusBadges = [
    { label: "Pending", value: breakdown.pending || 0, variant: "pending" as const },
    { label: "Processing", value: breakdown.processing || 0, variant: "processing" as const },
    { label: "Done", value: breakdown.completed || 0, variant: "success" as const },
    { label: "Failed", value: breakdown.failed || 0, variant: "error" as const },
    { label: "No Captions", value: breakdown.no_captions || 0, variant: "warning" as const },
    { label: "Skipped", value: breakdown.skipped || 0, variant: "secondary" as const },
    { label: "Rate Limited", value: breakdown.rate_limited || 0, variant: "rate_limited" as const },
  ];

  const handleScan = () => {
    scanSource.mutate(id);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Link
          href="/sources"
          className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back
        </Link>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-xl">{source.name}</CardTitle>
              <p className="text-sm text-muted-foreground mt-1">
                {source.platform} &middot; {source.external_id}
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => restartSourceMutation.mutate(id)}
                disabled={restartSourceMutation.isPending}
              >
                <RefreshCw className="mr-2 h-4 w-4" />
                Restart Failed
              </Button>
              <Button
                size="sm"
                onClick={handleScan}
                disabled={scanSource.isPending}
              >
                {scanSource.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Scan className="mr-2 h-4 w-4" />
                )}
                Scan Now
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <StatItem label="Videos" value={source.video_count} />
            <StatItem label="Wiki Pages" value={source.page_count} />
            <StatItem label="Platform" value={source.platform} />
            <StatItem
              label="Last Scanned"
              value={
                source.last_checked_at
                  ? new Date(source.last_checked_at).toLocaleDateString("vi-VN")
                  : "Never"
              }
            />
          </div>
          {source.url && (
            <div className="mt-4 text-sm">
              <span className="text-muted-foreground">URL: </span>
              <a
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                {source.url}
              </a>
            </div>
          )}

          {scanSource.data && (
            <div className="mt-4 rounded-lg border border-green-500/20 bg-green-500/5 p-3 text-sm">
              <p className="font-medium text-green-600">Scan completed</p>
              <p className="text-muted-foreground">
                {scanSource.data.new_items_found ?? 0} new videos found
                {(scanSource.data.restarted_rate_limited ?? 0) > 0 &&
                  `, ${scanSource.data.restarted_rate_limited} rate-limited restarted`}
                {(scanSource.data.restarted_failed ?? 0) > 0 &&
                  `, ${scanSource.data.restarted_failed} failed restarted`}
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Status Breakdown */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Status</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-3">
            {statusBadges.map((badge) => (
              <Badge key={badge.label} variant={badge.variant}>
                {badge.label}: {badge.value}
              </Badge>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Needs Attention */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-sm">
            <FileText className="h-4 w-4 text-orange-500" />
            Needs Attention ({needsAttentionTotal})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {itemsLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : (
            <>
              {memberOnlyCount > 0 && (
                <div className="flex items-center gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-3 py-2 mb-3 text-xs text-amber-700 dark:text-amber-400">
                  <Lock className="h-3.5 w-3.5 shrink-0" />
                  <span>{memberOnlyCount} member-only videos — transcripts unavailable</span>
                </div>
              )}

              {needsAttention.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-8 text-center">
                  <FileText className="h-8 w-8 text-muted-foreground mb-2" />
                  <p className="text-sm font-medium">No items need attention</p>
                </div>
              ) : (
                <div className="max-h-[400px] overflow-y-auto space-y-2 pr-1">
                  {needsAttention.map((item) => (
                    <AttentionItem
                      key={item.id}
                      item={item}
                      onSkip={(id) => skipItem.mutate(id)}
                      onRetry={(id) => retryItem.mutate(id)}
                      onAddTranscript={(id, title) =>
                        setTranscriptModal({ itemId: id, title })
                      }
                      isSkipPending={skipItem.isPending}
                      isRetryPending={retryItem.isPending}
                    />
                  ))}
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Add Transcript Modal */}
      <AddTranscriptModal
        isOpen={transcriptModal !== null}
        onClose={() => setTranscriptModal(null)}
        onSubmit={(text) => {
          if (transcriptModal) {
            submitTranscript.mutate({
              itemId: transcriptModal.itemId,
              payload: { transcript_text: text },
            });
            setTranscriptModal(null);
          }
        }}
        videoTitle={transcriptModal?.title || null}
        isPending={submitTranscript.isPending}
      />
    </div>
  );
}

function StatItem({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground uppercase tracking-wide">
        {label}
      </p>
      <p className="text-2xl font-bold">{value}</p>
    </div>
  );
}

function AttentionItem({
  item,
  onSkip,
  onRetry,
  onAddTranscript,
  isSkipPending,
  isRetryPending,
}: {
  item: SourceItem;
  onSkip: (id: string) => void;
  onRetry: (id: string) => void;
  onAddTranscript: (id: string, title: string | null) => void;
  isSkipPending: boolean;
  isRetryPending: boolean;
}) {
  const statusVariant = item.status === "failed"
    ? "error"
    : item.status === "no_captions"
    ? "warning"
    : item.status === "skipped"
    ? "secondary"
    : "rate_limited";

  return (
    <div className="flex items-start justify-between rounded-lg border p-3">
      <div className="space-y-1 min-w-0 flex-1 mr-4">
        <div className="flex items-center gap-2">
              <Badge variant={statusVariant as "error" | "warning" | "secondary" | "rate_limited"}>
                {item.status === "failed" ? "Failed" : item.status === "no_captions" ? "No Captions" : item.status === "skipped" ? "Skipped" : "Rate Limited"}
              </Badge>
          {item.retry_count > 1 && (
            <span className="text-xs text-muted-foreground">
              Retried: {item.retry_count}x
            </span>
          )}
        </div>
        <p className="text-sm font-medium truncate">
          {item.title || item.external_id}
        </p>
        {item.error_message && (
          <p className="text-xs text-muted-foreground truncate">
            {item.error_message}
          </p>
        )}
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {item.url && (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center hover:text-foreground"
            >
              <ExternalLink className="mr-1 h-3 w-3" />
              YouTube
            </a>
          )}
          {item.published_at && (
            <span>
              {new Date(item.published_at).toLocaleDateString("vi-VN")}
            </span>
          )}
        </div>
      </div>
      <div className="flex gap-1.5 shrink-0">
        {(item.status === "no_captions" || item.status === "failed") && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onAddTranscript(item.id, item.title)}
          >
            <FileText className="mr-1 h-3 w-3" />
            Add Transcript
          </Button>
        )}
        {item.status !== "skipped" && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onRetry(item.id)}
            disabled={isRetryPending}
          >
            <RotateCcw className="mr-1 h-3 w-3" />
            Retry
          </Button>
        )}
        {item.status !== "skipped" && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => onSkip(item.id)}
            disabled={isSkipPending}
          >
            <SkipForward className="mr-1 h-3 w-3" />
            Skip
          </Button>
        )}
      </div>
    </div>
  );
}
