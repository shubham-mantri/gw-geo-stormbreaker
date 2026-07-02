"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Target } from "lucide-react";

import { apiClient } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import type { Alert, Brand } from "@/lib/types";
import { formatCurrency, formatPct } from "@/lib/utils";
import { SoVTrend } from "@/components/charts/SoVTrend";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function KpiCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-semibold tabular-nums">{value}</div>
      </CardContent>
    </Card>
  );
}

function OverviewSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading overview">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i}>
            <CardHeader className="pb-2">
              <Skeleton className="h-4 w-24" />
            </CardHeader>
            <CardContent>
              <Skeleton className="h-8 w-20" />
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader>
          <Skeleton className="h-4 w-40" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-64 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}

function EmptyState() {
  return (
    <Card className="mx-auto mt-10 max-w-lg text-center">
      <CardHeader>
        <CardTitle>Set up your first brand</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Add a brand, its competitors, and seed topics to start measuring where
          AI recommends you. Your dashboard fills in once the first snapshot
          lands.
        </p>
        <Button asChild>
          <Link href="/onboarding">Start onboarding</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function CountStrip({ alerts }: { alerts: Alert[] }) {
  // The overview contract exposes KPIs only; the notification strip is derived
  // from the alerts feed by severity: red = issues, yellow = opportunities,
  // green = wins (ui-spec §3.1).
  const issues = alerts.filter((a) => a.severity === "red").length;
  const opportunities = alerts.filter((a) => a.severity === "yellow").length;
  const wins = alerts.filter((a) => a.severity === "green").length;

  return (
    <div className="flex flex-wrap items-center gap-x-8 gap-y-2 text-sm">
      <Link
        href="/alerts"
        className="inline-flex items-center gap-2 hover:underline"
      >
        <AlertTriangle className="h-4 w-4 text-warning" aria-hidden="true" />
        <span className="font-medium tabular-nums">{issues}</span> alerts
      </Link>
      <span className="inline-flex items-center gap-2 text-muted-foreground">
        <Target className="h-4 w-4" aria-hidden="true" />
        <span className="font-medium tabular-nums text-foreground">
          {opportunities}
        </span>{" "}
        open opportunities
      </span>
      <Link
        href="/alerts"
        className="inline-flex items-center gap-2 hover:underline"
      >
        <CheckCircle2 className="h-4 w-4 text-success" aria-hidden="true" />
        <span className="font-medium tabular-nums">{wins}</span> wins
      </Link>
    </div>
  );
}

export default function OverviewPage() {
  const { brandId, range } = useFilters();

  const brandsQuery = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
  });

  const activeBrandId = brandId ?? brandsQuery.data?.[0]?.id ?? null;
  const activeBrand: Brand | undefined = brandsQuery.data?.find(
    (b) => b.id === activeBrandId,
  );

  const overviewQuery = useQuery({
    queryKey: ["overview", activeBrandId, range],
    queryFn: () => apiClient(getToken).overview(activeBrandId as string, range),
    enabled: activeBrandId !== null,
  });

  const alertsQuery = useQuery({
    queryKey: ["alerts", activeBrandId],
    queryFn: () => apiClient(getToken).alerts(activeBrandId as string),
    enabled: activeBrandId !== null,
  });

  if (brandsQuery.isLoading) return <OverviewSkeleton />;

  if (!brandsQuery.data || brandsQuery.data.length === 0 || activeBrandId === null) {
    return <EmptyState />;
  }

  if (overviewQuery.isLoading || !overviewQuery.data) return <OverviewSkeleton />;

  const o = overviewQuery.data;
  // The backend's trend[].competitor is (1 − share_of_voice): ALL competitors combined, not any
  // single named rival. Label it honestly (PRD §13) rather than pinning it to competitors[0].
  const competitorLabel = "All competitors (combined)";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        {activeBrand ? (
          <p className="text-sm text-muted-foreground">{activeBrand.name}</p>
        ) : null}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard label="Share of Voice" value={formatPct(o.sov)} />
        <KpiCard label="Mention Rate" value={formatPct(o.mention_rate)} />
        <KpiCard label="AI Pipeline" value={formatCurrency(o.pipeline)} />
        <KpiCard label="Leads from AI" value={String(o.leads)} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Share-of-Voice trend — you vs. all competitors
          </CardTitle>
        </CardHeader>
        <CardContent>
          <SoVTrend data={o.trend} competitorLabel={competitorLabel} />
        </CardContent>
      </Card>

      <CountStrip alerts={alertsQuery.data ?? []} />
    </div>
  );
}
