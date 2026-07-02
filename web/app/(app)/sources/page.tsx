"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import { SourceMap } from "@/components/SourceMap";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function SourcesSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading sources">
      <Skeleton className="h-8 w-40" />
      <Card>
        <CardContent className="space-y-3 pt-6">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function EmptyState() {
  return (
    <Card className="mx-auto mt-10 max-w-lg text-center">
      <CardHeader>
        <CardTitle>No source data yet</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Once your brand is set up and the first measurement snapshot lands,
          the citation-source map appears here.
        </p>
        <Button asChild>
          <Link href="/onboarding">Start onboarding</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

export default function SourcesPage() {
  const { brandId, range } = useFilters();

  const brandsQuery = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
  });

  const activeBrandId = brandId ?? brandsQuery.data?.[0]?.id ?? null;

  const sourcesQuery = useQuery({
    queryKey: ["sources", activeBrandId, range],
    queryFn: () => apiClient(getToken).sources(activeBrandId as string, range),
    enabled: activeBrandId !== null,
  });

  if (brandsQuery.isLoading) return <SourcesSkeleton />;

  if (!brandsQuery.data || brandsQuery.data.length === 0 || activeBrandId === null) {
    return <EmptyState />;
  }

  if (sourcesQuery.isLoading || !sourcesQuery.data) return <SourcesSkeleton />;

  const sources = sourcesQuery.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Sources</h1>
        <p className="text-sm text-muted-foreground">
          Where AI cites you (and your competitors) — tells you where to seed
          next.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Citation-source map</CardTitle>
          <CardDescription>
            Rows shaded red show a competitor cited more than you — a seeding
            opportunity.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {sources.length === 0 ? (
            <p className="py-6 text-sm text-muted-foreground">
              No citation sources yet — they appear once measurement runs.
            </p>
          ) : (
            <SourceMap sources={sources} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
