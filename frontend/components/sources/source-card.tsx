"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Radio, ExternalLink } from "lucide-react";
import type { Source } from "@/types";

interface SourceCardProps {
  source: Source;
  onToggle: (id: string, isActive: boolean) => void;
}

export function SourceCard({ source, onToggle }: SourceCardProps) {
  const isActive = source.status === "active";

  return (
    <Link href={`/sources/${source.id}`} className="block">
      <Card className="hover:shadow-md transition-shadow cursor-pointer">
        <CardHeader className="flex flex-row items-center justify-between pb-2">
          <div className="flex items-center gap-2">
            <Radio className="h-5 w-5 text-primary" />
            <CardTitle className="text-base">{source.name}</CardTitle>
          </div>
          <Badge variant={isActive ? "success" : "secondary"}>
            {isActive ? "Active" : "Inactive"}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="text-sm text-muted-foreground space-y-1">
            <p>Platform: {source.platform}</p>
            {source.url && (
              <p className="truncate">
                URL:{" "}
                <span className="text-primary inline-flex items-center gap-1">
                  {source.url} <ExternalLink className="h-3 w-3" />
                </span>
              </p>
            )}
          </div>
          <div className="flex justify-between gap-6">
            <span />
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onToggle(source.id, isActive);
              }}
            >
              {isActive ? "Deactivate" : "Activate"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
