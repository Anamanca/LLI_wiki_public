"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { Header } from "@/components/layout/header";
import { MarkdownRenderer } from "@/components/wiki/markdown-renderer";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { usePage } from "@/hooks/use-pages";
import { formatDate } from "@/lib/utils";
import { ArrowLeft, ExternalLink, Image as ImageIcon, Youtube } from "lucide-react";

export default function WikiDetailPage() {
  const { slug } = useParams<{ slug: string }>();
  const { data: page, isLoading, error } = usePage(slug);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-96 w-full rounded-lg" />
      </div>
    );
  }

  if (error || !page) {
    return (
      <div className="space-y-6">
        <Link href="/wiki" className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="mr-1 h-4 w-4" />
          Back to Wiki
        </Link>
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error?.message || "Page not found"}
        </div>
      </div>
    );
  }

  const linkedPages = page.linked_pages || [];

  return (
    <div className="space-y-6">
      <Link href="/wiki" className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="mr-1 h-4 w-4" />
        Back to Wiki
      </Link>

      <div className="flex flex-col lg:flex-row gap-6">
        <div className="flex-1 space-y-6 min-w-0">
          <div>
            <Header title={page.title} />
            <div className="flex items-center gap-3 text-sm text-muted-foreground mt-1">
              {page.source_name && (
                <Badge variant="secondary">{page.source_name}</Badge>
              )}
              {page.published_at && <span>{formatDate(page.published_at)}</span>}
              <span
                className="ml-2 text-xs text-muted-foreground/50 cursor-pointer select-all font-mono"
                title="Copy page ID for debugging"
              >
                {page.id}
              </span>
            </div>
          </div>

          {page.summary && (
            <Card>
              <CardContent className="pt-6">
                <p className="text-muted-foreground">{page.summary}</p>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardContent className="pt-6">
              {page.content_markdown ? (
                <MarkdownRenderer content={page.content_markdown} />
              ) : (
                <p className="text-muted-foreground italic">No content available.</p>
              )}
            </CardContent>
          </Card>

          {page.media_assets && page.media_assets.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">Hình ảnh từ video</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {page.media_assets.map((asset) => (
                    <div key={asset.id} className="rounded-lg border overflow-hidden bg-muted">
                      {asset.url ? (
                        <a href={asset.url} target="_blank" rel="noopener noreferrer" className="block">
                          <img
                            src={asset.url}
                            alt={asset.description || asset.filename}
                            className="w-full h-auto object-cover aspect-video"
                            loading="lazy"
                          />
                          {asset.description && (
                            <p className="px-2 py-1.5 text-xs text-muted-foreground line-clamp-2 border-t">
                              {asset.description}
                            </p>
                          )}
                        </a>
                      ) : (
                        <div className="aspect-video flex items-center justify-center">
                          <ImageIcon className="h-8 w-8 text-muted-foreground" />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        <div className="lg:w-56 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Related Pages</CardTitle>
            </CardHeader>
            <CardContent>
              {linkedPages.length === 0 ? (
                <p className="text-sm text-muted-foreground">No related pages yet.</p>
              ) : (
                <div className="space-y-1">
                  {linkedPages.map((lp) => (
                    <Link
                      key={lp.id}
                      href={`/wiki/${lp.slug}`}
                      className="block rounded-md px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground transition-colors"
                    >
                      <div className="flex items-center gap-1.5">
                        <ExternalLink className="h-3 w-3 flex-shrink-0 text-muted-foreground" />
                        <span className="line-clamp-1 font-medium">{lp.title}</span>
                      </div>
                      {lp.relation_type && (
                        <span className="text-xs text-muted-foreground ml-[18px]">{lp.relation_type}</span>
                      )}
                    </Link>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Source</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {page.source_video_url && (
                <a
                  href={page.source_video_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-sm text-red-500 hover:text-red-600 hover:underline transition-colors"
                >
                  <Youtube className="h-4 w-4 flex-shrink-0" />
                  <span className="line-clamp-1">Watch on YouTube</span>
                  <ExternalLink className="h-3 w-3 flex-shrink-0" />
                </a>
              )}
              {page.source_url && (
                <a
                  href={page.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 text-sm text-primary hover:underline transition-colors"
                >
                  <ExternalLink className="h-3 w-3 flex-shrink-0" />
                  <span className="line-clamp-1">{page.source_name || "Channel"}</span>
                </a>
              )}
              {!page.source_video_url && !page.source_url && page.source_name && (
                <span className="text-sm text-muted-foreground">{page.source_name}</span>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
