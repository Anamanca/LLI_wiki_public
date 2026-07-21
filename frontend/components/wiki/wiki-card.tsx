"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatDate, truncate } from "@/lib/utils";
import type { PageSummary, SearchResult } from "@/types";

interface WikiCardProps {
  page: PageSummary | SearchResult;
}

export function WikiCard({ page }: WikiCardProps) {
  return (
    <Link href={`/wiki/${page.slug}`}>
      <Card className="hover:shadow-md transition-shadow cursor-pointer h-full">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between gap-2">
            <CardTitle className="text-base line-clamp-1">
              {page.title}
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-sm text-muted-foreground line-clamp-3">
            {truncate(page.summary || "", 200)}
          </p>
          <div className="flex items-center justify-between">
            <Badge variant="secondary" className="text-xs">
              {page.source_name}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {"updated_at" in page && page.updated_at
                ? formatDate(page.updated_at)
                : "published_at" in page && page.published_at
                ? formatDate(page.published_at)
                : "created_at" in page && page.created_at
                ? formatDate(page.created_at)
                : ""}
            </span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
