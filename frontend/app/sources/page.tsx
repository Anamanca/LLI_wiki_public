"use client";

import { useState } from "react";
import { Header } from "@/components/layout/header";
import { SourceCard } from "@/components/sources/source-card";
import { SourceForm } from "@/components/sources/source-form";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Plus } from "lucide-react";
import { useSources, useCreateSource, useUpdateSource } from "@/hooks/use-sources";

export default function SourcesPage() {
  const { data, isLoading, error } = useSources();
  const createSource = useCreateSource();
  const updateSource = useUpdateSource();
  const [formOpen, setFormOpen] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <Header
          title="Sources"
          description="Manage your content sources and ingestion channels"
        />
        <Button onClick={() => setFormOpen(true)}>
          <Plus className="mr-2 h-4 w-4" />
          Add Source
        </Button>
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          Failed to load sources: {error.message}
        </div>
      )}

      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-48 w-full rounded-lg" />
          ))}
        </div>
      ) : !data || data.sources.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <p className="text-lg font-medium">No sources yet</p>
          <p className="text-sm text-muted-foreground mt-1">
            Add your first source to get started.
          </p>
          <Button className="mt-4" onClick={() => setFormOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Add Source
          </Button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {data.sources.map((source) => (
            <SourceCard
              key={source.id}
              source={source}
              onToggle={(id, isActive) =>
                updateSource.mutate({ id, status: isActive ? "inactive" : "active" })
              }
            />
          ))}
        </div>
      )}

      <SourceForm
        open={formOpen}
        onOpenChange={setFormOpen}
        onSubmit={(payload) => createSource.mutate(payload)}
        isSubmitting={createSource.isPending}
      />
    </div>
  );
}
