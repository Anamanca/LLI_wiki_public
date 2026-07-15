"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useCronJobs, useStartCronJob, useStopCronJob } from "@/hooks/use-cron-jobs";
import { Clock, Play, Square, RefreshCw, CheckCircle, XCircle, AlertTriangle, Timer } from "lucide-react";
import { formatDate } from "@/lib/utils";
import type { CronJobStatus } from "@/types";

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "running":
      return <CheckCircle className="h-4 w-4 text-green-500" />;
    case "completed":
      return <CheckCircle className="h-4 w-4 text-green-500" />;
    case "scheduled":
      return <Clock className="h-4 w-4 text-blue-500" />;
    case "stopped":
      return <XCircle className="h-4 w-4 text-red-500" />;
    case "error":
      return <AlertTriangle className="h-4 w-4 text-red-500" />;
    case "no_workers":
      return <XCircle className="h-4 w-4 text-red-500" />;
    case "crontab_missing":
      return <AlertTriangle className="h-4 w-4 text-yellow-500" />;
    case "not_found":
      return <XCircle className="h-4 w-4 text-red-500" />;
    default:
      return <Timer className="h-4 w-4 text-muted-foreground" />;
  }
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { variant: "success" | "error" | "warning" | "processing" | "secondary"; label: string }> = {
    running: { variant: "success", label: "Running" },
    completed: { variant: "success", label: "Done Today" },
    scheduled: { variant: "secondary", label: "Scheduled" },
    stopped: { variant: "error", label: "Stopped" },
    error: { variant: "error", label: "Error" },
    no_workers: { variant: "error", label: "No Workers" },
    crontab_missing: { variant: "warning", label: "Crontab Missing" },
    not_found: { variant: "error", label: "CronJob Missing" },
  };
  const info = map[status] || { variant: "secondary" as const, label: status };
  return <Badge variant={info.variant}>{info.label}</Badge>;
}

function JobTypeBadge({ jobType }: { jobType: string }) {
  if (jobType === "kubernetes_cronjob") {
    return <Badge variant="secondary" className="text-xs">K8s CronJob</Badge>;
  }
  if (jobType === "crontab") {
    return <Badge variant="secondary" className="text-xs">System Crontab</Badge>;
  }
  return <Badge variant="secondary" className="text-xs">Background Task</Badge>;
}

function CronJobRow({ job }: { job: CronJobStatus }) {
  const startJob = useStartCronJob();
  const stopJob = useStopCronJob();
  const isBusy = startJob.isPending || stopJob.isPending;

  return (
    <div className="flex items-center justify-between rounded-lg border p-4">
      <div className="flex items-start gap-3 min-w-0 flex-1">
        <div className="mt-0.5 shrink-0">
          <StatusIcon status={job.status} />
        </div>
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm font-medium">{job.name}</p>
            <StatusBadge status={job.status} />
            <JobTypeBadge jobType={job.job_type} />
          </div>
          <p className="text-xs text-muted-foreground">{job.description}</p>
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {job.schedule}
            </span>
            {job.last_run && (
              <span>Last run: {formatDate(job.last_run)}</span>
            )}
            {job.alive_workers !== undefined && (
              <span>{job.alive_workers} workers alive</span>
            )}
            {job.crontab_active !== undefined && !job.crontab_active && job.job_type === "crontab" && (
              <span className="text-yellow-500">Crontab not detected on host</span>
            )}
          </div>
          {job.error && (
            <p className="text-xs text-red-500">{job.error}</p>
          )}
        </div>
      </div>
      {job.managed && (
        <div className="flex items-center gap-2 shrink-0 ml-4">
          <Button
            variant="outline"
            size="sm"
            onClick={() => startJob.mutate(job.job_id)}
            disabled={isBusy}
            title="Start now"
          >
            {startJob.isPending && startJob.variables === job.job_id ? (
              <RefreshCw className="h-3 w-3 animate-spin" />
            ) : (
              <Play className="h-3 w-3" />
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => stopJob.mutate(job.job_id)}
            disabled={isBusy}
            title="Stop"
          >
            <Square className="h-3 w-3" />
          </Button>
        </div>
      )}
    </div>
  );
}

export function CronJobsPanel() {
  const { data: jobs, isLoading, error } = useCronJobs();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Timer className="h-5 w-5" />
          Cron Jobs & Background Tasks
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full" />
            ))}
          </div>
        ) : error ? (
          <p className="text-sm text-red-500">Failed to load cron jobs: {(error as Error).message}</p>
        ) : !jobs?.length ? (
          <p className="text-sm text-muted-foreground">No cron jobs configured.</p>
        ) : (
          <div className="space-y-3">
            {jobs.map((job) => (
              <CronJobRow key={job.job_id} job={job} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
