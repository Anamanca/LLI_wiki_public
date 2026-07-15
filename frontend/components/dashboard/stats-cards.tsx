"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import type { Progress } from "@/types";
import { Radio, BookOpen, FileCheck, AlertTriangle } from "lucide-react";

interface StatsCardsProps {
  progress?: Progress;
  isLoading: boolean;
}

export function StatsCards({ progress, isLoading }: StatsCardsProps) {
  if (isLoading) {
    return (
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i}>
            <CardHeader className="pb-2">
              <Skeleton className="h-4 w-24" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-8 w-16" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (!progress) {
    return (
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground">No data available</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const stats = [
    {
      title: "Active Sources",
      value: progress.per_source.length,
      icon: Radio,
      color: "text-blue-500",
    },
    {
      title: "Done Today",
      value: progress.global.done_today,
      icon: FileCheck,
      color: "text-green-500",
    },
    {
      title: "Processing",
      value: progress.global.processing,
      icon: BookOpen,
      color: "text-purple-500",
    },
    {
      title: "Alerts",
      value: progress.alerts.length,
      icon: AlertTriangle,
      color: "text-orange-500",
    },
  ];

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
      {stats.map((stat) => (
        <Card key={stat.title}>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">{stat.title}</CardTitle>
            <stat.icon className={cn("h-4 w-4", stat.color)} />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {stat.value}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
