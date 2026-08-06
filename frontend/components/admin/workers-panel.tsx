"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkers } from "@/hooks/use-workers";
import { Cpu, Activity, CheckCircle, XCircle, Clock, AlertTriangle } from "lucide-react";
import type { WorkerInfo } from "@/types";

// Worker type is now explicitly stored in the heartbeat table.
// This replaces the old numeric-ID convention (1-98 cpu, 99 gpu, 101+ wiki).

function workerLabel(worker: WorkerInfo): string {
  if (worker.worker_type === "cpu") return `CPU`;
  if (worker.worker_type === "wiki") return `Wiki`;
  if (worker.worker_type === "gpu") return `GPU`;
  return worker.worker_id;
}

function workerGroup(worker: WorkerInfo): string {
  if (worker.worker_type) return worker.worker_type;
  return "unknown";
}

function workerShortId(worker: WorkerInfo): string {
  const parts = worker.worker_id.split("-");
  return parts.slice(-2).join("-");
}

function StatusBadge({ status, alive }: { status: string; alive: boolean }) {
  if (!alive) return <Badge variant="error">Dead</Badge>;
  if (status === "wiki") return <Badge variant="processing">Wiki</Badge>;
  if (status === "idle") return <Badge variant="secondary">Idle</Badge>;
  if (status === "transcribing") return <Badge variant="warning">Transcribing</Badge>;
  return <Badge variant="processing">{status}</Badge>;
}

function WorkerCard({ worker }: { worker: WorkerInfo }) {
  const group = workerGroup(worker);
  const groupColor = group === "cpu" ? "border-l-blue-500" : group === "gpu" ? "border-l-purple-500" : "border-l-green-500";

  return (
    <div className={`rounded-lg border-l-4 ${groupColor} bg-muted/30 px-3 py-2 space-y-1`}>
      {/* Row 1: icon + label + status badge */}
      <div className="flex items-center gap-1.5 min-w-0">
        {worker.alive ? (
          <CheckCircle className="h-3.5 w-3.5 text-green-500 shrink-0" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0" />
        )}
        <span className="text-xs font-mono font-medium truncate">{workerLabel(worker)}</span>
        <StatusBadge status={worker.status} alive={worker.alive} />
        {worker.error_message && (
          <span title={worker.error_message} className="shrink-0">
            <AlertTriangle className="h-3 w-3 text-red-500" />
          </span>
        )}
      </div>
      {/* Row 2: stage + metrics */}
      <div className="flex items-center justify-between gap-1 text-xs text-muted-foreground">
        <span className="truncate">{worker.current_stage || worker.status || "idle"}</span>
        <span className="flex items-center gap-2 shrink-0">
          {worker.cpu_percent > 0 && (
            <span className="flex items-center gap-0.5">
              <Cpu className="h-3 w-3" />
              {worker.cpu_percent}%
            </span>
          )}
          <span className="flex items-center gap-0.5">
            <Clock className="h-3 w-3" />
            {worker.heartbeat_ago_secs}s
          </span>
        </span>
      </div>
    </div>
  );
}

export function WorkersPanel() {
  const { data, isLoading, error } = useWorkers();

  const workers = data?.workers || [];
  const cpuWorkers = workers.filter((w) => workerGroup(w) === "cpu");
  const gpuWorker = workers.find((w) => workerGroup(w) === "gpu");
  const wikiConsumers = workers.filter((w) => workerGroup(w) === "wiki");
  const alive = workers.filter((w) => w.alive).length;
  const dead = workers.filter((w) => !w.alive).length;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Activity className="h-5 w-5" />
          Workers
        </CardTitle>
        {!isLoading && (
          <div className="flex items-center gap-2 text-sm">
            <Badge variant="success">{alive} alive</Badge>
            {dead > 0 && <Badge variant="error">{dead} dead</Badge>}
          </div>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : error ? (
          <p className="text-sm text-red-500">Failed to load workers</p>
        ) : (
          <div className="space-y-3">
            {/* GPU Worker */}
            {gpuWorker && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1.5">GPU Worker</p>
                <WorkerCard worker={gpuWorker} />
              </div>
            )}

            {/* CPU Workers */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1.5">
                CPU Workers ({cpuWorkers.filter((w) => w.alive).length}/{cpuWorkers.length})
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-1.5">
                {cpuWorkers.map((w) => (
                  <WorkerCard key={w.worker_id} worker={w} />
                ))}
              </div>
            </div>

            {/* Wiki Consumers */}
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1.5">
                Wiki Consumers ({wikiConsumers.filter((w) => w.alive).length}/{wikiConsumers.length})
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-1.5">
                {wikiConsumers.map((w) => (
                  <WorkerCard key={w.worker_id} worker={w} />
                ))}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
