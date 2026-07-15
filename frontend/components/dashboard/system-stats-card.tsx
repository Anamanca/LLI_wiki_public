"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchSystemStats } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Cpu, HardDrive, MemoryStick } from "lucide-react";

function progressColor(value: number): string {
  if (value >= 90) return "bg-red-500";
  if (value >= 70) return "bg-yellow-500";
  return "bg-green-500";
}

function ProgressBar({ value, max, label, icon: Icon }: {
  value: number;
  max: number;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <Icon className="h-3.5 w-3.5" />
          {label}
        </span>
        <span className="font-mono tabular-nums">
          {value.toFixed(1)} / {max.toFixed(0)} GB ({pct.toFixed(0)}%)
        </span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${progressColor(pct)}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function CpuBar({ value }: { value: number }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <Cpu className="h-3.5 w-3.5" />
          CPU
        </span>
        <span className="font-mono tabular-nums">{value.toFixed(0)}%</span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${progressColor(value)}`}
          style={{ width: `${value}%` }}
        />
      </div>
    </div>
  );
}

export function SystemStatsCard() {
  const { data, isLoading } = useQuery({
    queryKey: queryKeys.systemStats,
    queryFn: fetchSystemStats,
    refetchInterval: 5000,
  });

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">System Resources</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (!data) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">System Resources</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <CpuBar value={data.cpu_percent} />
        <ProgressBar
          value={data.ram_used_gb}
          max={data.ram_total_gb}
          label="RAM"
          icon={MemoryStick}
        />
        <ProgressBar
          value={data.disk_used_gb}
          max={data.disk_total_gb}
          label="Disk"
          icon={HardDrive}
        />
      </CardContent>
    </Card>
  );
}
