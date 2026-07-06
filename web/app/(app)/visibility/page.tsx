"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api";
import type { Role } from "@/lib/auth";
import { getRole, getToken } from "@/lib/auth";
import { ALL_ENGINES, useFilters } from "@/lib/filters";
import { EngineTable } from "@/components/EngineTable";
import { PromptDrawer } from "@/components/PromptDrawer";
import { RunMeasurementButton } from "@/components/RunMeasurementButton";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function VisibilitySkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading visibility">
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

function EmptyState({
  brandId,
  role,
}: {
  brandId: string | null;
  role: Role | null;
}) {
  return (
    <Card className="mx-auto mt-10 max-w-lg text-center">
      <CardHeader>
        <CardTitle>No visibility data yet</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Once your brand is set up and the first measurement snapshot lands,
          per-engine visibility appears here.
        </p>
        <div className="flex flex-col items-center gap-3">
          <Button asChild>
            <Link href="/onboarding">Start onboarding</Link>
          </Button>
          {/* Trigger the first snapshot for an already-onboarded brand; disabled
              until there's a brand to measure. */}
          <RunMeasurementButton brandId={brandId} role={role} />
        </div>
      </CardContent>
    </Card>
  );
}

export default function VisibilityPage() {
  const { brandId, range, engine } = useFilters();
  const role = getRole();

  const brandsQuery = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
  });

  const activeBrandId = brandId ?? brandsQuery.data?.[0]?.id ?? null;

  const visibilityQuery = useQuery({
    queryKey: ["visibility", activeBrandId, range],
    queryFn: () =>
      apiClient(getToken).visibility(activeBrandId as string, { range }),
    enabled: activeBrandId !== null,
  });

  if (brandsQuery.isLoading) return <VisibilitySkeleton />;

  if (!brandsQuery.data || brandsQuery.data.length === 0 || activeBrandId === null) {
    return <EmptyState brandId={activeBrandId} role={role} />;
  }

  if (visibilityQuery.isLoading || !visibilityQuery.data) {
    return <VisibilitySkeleton />;
  }

  const { engines, prompts } = visibilityQuery.data;
  const filteredEngines =
    engine === ALL_ENGINES
      ? engines
      : engines.filter((e) => e.engine === engine);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Visibility</h1>
        <p className="text-sm text-muted-foreground">
          Where you stand on each engine — every rate shows its confidence
          interval and sample size.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">By engine</CardTitle>
        </CardHeader>
        <CardContent>
          {filteredEngines.length === 0 ? (
            <p className="py-6 text-sm text-muted-foreground">
              No data for the selected engine in this range.
            </p>
          ) : (
            <EngineTable engines={filteredEngines} />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Prompt-level detail</CardTitle>
          <CardDescription>
            Expand a prompt to see its sampled-answer count and metrics.
          </CardDescription>
        </CardHeader>
        <CardContent className="px-0">
          <PromptDrawer prompts={prompts} />
        </CardContent>
      </Card>
    </div>
  );
}
