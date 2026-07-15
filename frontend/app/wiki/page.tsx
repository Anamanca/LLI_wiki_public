"use client";

import { useState } from "react";
import { Header } from "@/components/layout/header";
import { WikiCard } from "@/components/wiki/wiki-card";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectItem } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { usePages } from "@/hooks/use-pages";
import { useSources } from "@/hooks/use-sources";

export default function WikiPage() {
  const [searchQuery, setSearchQuery] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [sortBy, setSortBy] = useState("published_at");
  const [sortOrder, setSortOrder] = useState("desc");
  const [page, setPage] = useState(1);
  const perPage = 20;

  const { data: sourcesData } = useSources();
  const sources = sourcesData?.sources || [];

  const { data, isLoading } = usePages({
    source_id: sourceId || undefined,
    search: searchQuery || undefined,
    sort_by: sortBy,
    sort_order: sortOrder,
    page,
    per_page: perPage,
  });

  const items = data?.items;
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / perPage);

  return (
    <div className="space-y-6">
      <Header title="Wiki" description="Browse and search your knowledge base" />

      <div className="flex flex-wrap gap-3">
        <Input
          placeholder="Search pages..."
          value={searchQuery}
          onChange={(e) => { setSearchQuery(e.target.value); setPage(1); }}
          className="max-w-sm"
        />

        <Select
          value={sourceId}
          displayValue={sourceId ? sources.find((s) => s.id === sourceId)?.name : undefined}
          onValueChange={(v) => { setSourceId(v); setPage(1); }}
          placeholder="All Sources"
          className="w-[180px]"
        >
          <SelectItem value="">All Sources</SelectItem>
          {sources.map((s) => (
            <SelectItem key={s.id} value={s.id}>
              {s.name}
            </SelectItem>
          ))}
        </Select>

        <Select
          value={sortBy}
          onValueChange={setSortBy}
          className="w-[160px]"
        >
          <SelectItem value="published_at">Published date</SelectItem>
          <SelectItem value="updated_at">Updated date</SelectItem>
          <SelectItem value="created_at">Created date</SelectItem>
        </Select>

        <Select
          value={sortOrder}
          onValueChange={setSortOrder}
          className="w-[120px]"
        >
          <SelectItem value="desc">Newest first</SelectItem>
          <SelectItem value="asc">Oldest first</SelectItem>
        </Select>
      </div>

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-32 w-full rounded-lg" />
          ))}
        </div>
      ) : !items || items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <p className="text-lg font-medium">No pages found</p>
          <p className="text-sm text-muted-foreground mt-1">
            {searchQuery
              ? "Try a different search term."
              : sourceId
                ? "No pages for this source yet."
                : "Add sources and ingest content to build your wiki."}
          </p>
        </div>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {items.map((item) => (
              <WikiCard key={item.slug} page={item} />
            ))}
          </div>

          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t pt-4">
              <p className="text-sm text-muted-foreground">
                Showing page {page} of {totalPages} ({total} pages total)
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage(page + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
