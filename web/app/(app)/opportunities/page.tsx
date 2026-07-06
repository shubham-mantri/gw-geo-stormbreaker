"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Lightbulb, RefreshCw } from "lucide-react";

import { apiClient } from "@/lib/api";
import type { Role } from "@/lib/auth";
import { getRole, getToken } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import type { Brand, Opportunity } from "@/lib/types";
import { formatPct } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** Roles that may refresh/act (backend gates both on `role >= editor`). */
const CAN_ACT_ROLES: Role[] = ["owner", "admin", "editor"];

function OpportunitiesSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading opportunities">
      <Skeleton className="h-8 w-40" />
      <Card>
        <CardContent className="space-y-3 pt-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
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
        <CardTitle>No brand yet</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Once your brand is set up and the first measurement snapshot lands,
          your ranked opportunities appear here.
        </p>
        <Button asChild>
          <Link href="/onboarding">Start onboarding</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function OpportunityRow({
  opportunity,
  canAct,
  spawnedContentId,
  isActing,
  onAct,
}: {
  opportunity: Opportunity;
  canAct: boolean;
  spawnedContentId: string | undefined;
  isActing: boolean;
  onAct: (id: string) => void;
}) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 px-6 py-4">
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-medium">{opportunity.title}</p>
        <p className="text-sm text-muted-foreground">{opportunity.rationale}</p>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Badge variant="secondary" className="tabular-nums">
            {formatPct(opportunity.est_impact)} est. impact
          </Badge>
          <Badge variant="outline">{opportunity.engine ?? "all engines"}</Badge>
        </div>
      </div>
      <div className="shrink-0">
        {spawnedContentId ? (
          <Button asChild variant="outline" size="sm">
            <Link href={`/content?content_id=${encodeURIComponent(spawnedContentId)}`}>
              Review draft
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Link>
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            disabled={!canAct || isActing}
            onClick={() => onAct(opportunity.id)}
            title={!canAct ? "You need editor access to act on opportunities" : undefined}
          >
            {isActing ? "Drafting…" : "Fix this"}
            {!isActing ? <ArrowRight className="h-4 w-4" aria-hidden="true" /> : null}
          </Button>
        )}
      </div>
    </li>
  );
}

/**
 * Opportunities (ui-spec §3.4) — the bridge from insight to action: a brand's ranked visibility
 * gaps, a **Refresh** that (re)ranks them from live data (`POST …/opportunities/refresh`, 202 →
 * re-fetch), and a per-row **Fix this** (`POST /opportunities/{id}/act`) that spawns a pre-scoped
 * draft and links straight to the Content screen for it. Refresh + act require `role >= editor`
 * server-side, so both are disabled below admin/editor (visible, not hidden — mirrors the Settings
 * panels' role gate).
 */
export default function OpportunitiesPage() {
  const { brandId } = useFilters();
  const role = getRole();
  const canAct = role !== null && CAN_ACT_ROLES.includes(role);
  const queryClient = useQueryClient();

  const [spawned, setSpawned] = useState<Record<string, string>>({});
  const [actError, setActError] = useState<string | null>(null);

  const brandsQuery = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
  });

  const activeBrandId = brandId ?? brandsQuery.data?.[0]?.id ?? null;
  const activeBrand: Brand | undefined = brandsQuery.data?.find(
    (b) => b.id === activeBrandId,
  );

  const opportunitiesQuery = useQuery({
    queryKey: ["opportunities", activeBrandId],
    queryFn: () => apiClient(getToken).opportunities(activeBrandId as string),
    enabled: activeBrandId !== null,
  });

  const refreshMutation = useMutation({
    mutationFn: () => apiClient(getToken).refreshOpportunities(activeBrandId as string),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["opportunities", activeBrandId] }),
  });

  const actMutation = useMutation({
    mutationFn: (opportunityId: string) =>
      apiClient(getToken).actOnOpportunity(opportunityId),
    onSuccess: (result, opportunityId) => {
      setActError(null);
      setSpawned((m) => ({ ...m, [opportunityId]: result.content_id }));
    },
    onError: (err) =>
      setActError(err instanceof Error ? err.message : "Failed to draft content"),
  });

  if (brandsQuery.isLoading) return <OpportunitiesSkeleton />;

  if (!brandsQuery.data || brandsQuery.data.length === 0 || activeBrandId === null) {
    return <EmptyState />;
  }

  if (opportunitiesQuery.isLoading || !opportunitiesQuery.data) {
    return <OpportunitiesSkeleton />;
  }

  // Backend already orders by est_impact desc; sort defensively so the ranking holds regardless.
  const opportunities = [...opportunitiesQuery.data].sort(
    (a, b) => b.est_impact - a.est_impact,
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Opportunities</h1>
          <p className="text-sm text-muted-foreground">
            Ranked visibility gaps for {activeBrand?.name ?? "your brand"} — the
            bridge from insight to action.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          disabled={!canAct || refreshMutation.isPending}
          onClick={() => refreshMutation.mutate()}
          title={!canAct ? "You need editor access to refresh opportunities" : undefined}
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          {refreshMutation.isPending ? "Refreshing…" : "Refresh"}
        </Button>
      </div>

      {refreshMutation.isSuccess ? (
        <p className="text-sm text-muted-foreground">
          Refresh queued — the ranking updates from your latest visibility data.
        </p>
      ) : null}
      {refreshMutation.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Couldn&apos;t refresh opportunities. Please try again.
        </p>
      ) : null}
      {actError ? (
        <p role="alert" className="text-sm text-destructive">
          {actError}
        </p>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            <Lightbulb className="mr-2 inline h-4 w-4 align-[-2px]" aria-hidden="true" />
            Ranked by estimated impact
          </CardTitle>
          <CardDescription>
            &ldquo;Fix this&rdquo; drafts pre-scoped content and opens it in the
            Content workspace.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {opportunities.length === 0 ? (
            <p className="px-6 py-6 text-sm text-muted-foreground">
              No opportunities yet — run a refresh to generate them from your
              latest visibility data.
            </p>
          ) : (
            <ol className="divide-y">
              {opportunities.map((o) => (
                <OpportunityRow
                  key={o.id}
                  opportunity={o}
                  canAct={canAct}
                  spawnedContentId={spawned[o.id]}
                  isActing={actMutation.isPending && actMutation.variables === o.id}
                  onAct={(id) => actMutation.mutate(id)}
                />
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      {!canAct ? (
        <p className="text-xs text-muted-foreground">
          You need editor access to refresh or act on opportunities.
        </p>
      ) : null}
    </div>
  );
}
