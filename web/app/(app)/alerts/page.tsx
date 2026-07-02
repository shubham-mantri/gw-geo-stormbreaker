"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Info, type LucideIcon } from "lucide-react";

import { apiClient } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import type { Alert, AlertSeverity } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

function AlertsSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading alerts">
      <Skeleton className="h-8 w-40" />
      <Card>
        <CardContent className="space-y-3 pt-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
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
        <CardTitle>Set up your first brand</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Once your brand is set up and measuring, drift and win notifications appear here.
        </p>
        <Button asChild>
          <Link href="/onboarding">Start onboarding</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

type SeverityStyle = { icon: LucideIcon; className: string; label: string };

/** Severity -> icon/colour/label (ui-spec §3.7: 🔴 drift / 🟡 heads-up / 🟢 win). */
const SEVERITY_STYLES: Record<string, SeverityStyle> = {
  red: { icon: AlertTriangle, className: "text-destructive", label: "Issue" },
  yellow: { icon: Info, className: "text-warning", label: "Heads up" },
  green: { icon: CheckCircle2, className: "text-success", label: "Win" },
};

function severityStyle(severity: AlertSeverity): SeverityStyle {
  return (
    SEVERITY_STYLES[severity] ?? {
      icon: Info,
      className: "text-muted-foreground",
      label: "Notice",
    }
  );
}

function formatTs(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function AlertRow({ alert }: { alert: Alert }) {
  const { icon: Icon, className, label } = severityStyle(alert.severity);
  return (
    <li data-severity={alert.severity} className="flex items-start gap-3 px-6 py-4">
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", className)} aria-hidden="true" />
      <div className="flex-1 space-y-0.5">
        <p className="text-sm">{alert.message}</p>
        <p className="text-xs text-muted-foreground">{formatTs(alert.ts)}</p>
      </div>
      <span className="sr-only">{label}</span>
    </li>
  );
}

/** Alerts (ui-spec §3.7) — a severity-coloured feed of drift/win notifications. */
export default function AlertsPage() {
  const { brandId } = useFilters();

  const brandsQuery = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
  });

  const activeBrandId = brandId ?? brandsQuery.data?.[0]?.id ?? null;

  const alertsQuery = useQuery({
    queryKey: ["alerts", activeBrandId],
    queryFn: () => apiClient(getToken).alerts(activeBrandId as string),
    enabled: activeBrandId !== null,
  });

  if (brandsQuery.isLoading) return <AlertsSkeleton />;

  if (!brandsQuery.data || brandsQuery.data.length === 0 || activeBrandId === null) {
    return <EmptyState />;
  }

  if (alertsQuery.isLoading || !alertsQuery.data) return <AlertsSkeleton />;

  const alerts = alertsQuery.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Alerts</h1>
        <p className="text-sm text-muted-foreground">
          Drift and win notifications — also pushed to email/Slack as they happen.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent activity</CardTitle>
          <CardDescription>Newest first.</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {alerts.length === 0 ? (
            <p className="px-6 py-6 text-sm text-muted-foreground">
              No alerts yet — you&apos;ll see drift and win notifications here.
            </p>
          ) : (
            <ul className="divide-y">
              {alerts.map((a, i) => (
                <AlertRow key={`${a.ts}-${i}`} alert={a} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
