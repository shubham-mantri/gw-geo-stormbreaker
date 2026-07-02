"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { useFilters } from "@/lib/filters";
import type { Pipeline, TopAnswer } from "@/lib/types";
import { formatCurrency, formatPct } from "@/lib/utils";
import { ExportButton, type ExportRow } from "@/components/ExportButton";
import { MethodBreakdown } from "@/components/MethodBreakdown";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const METHOD_CSV_LABEL: Record<string, string> = {
  direct: "Direct referral",
  citation_linked: "Citation-linked",
  assisted: "Assisted",
  holdout_incremental: "Incremental (causal)",
};

function KpiCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-3xl font-semibold tabular-nums">{value}</div>
        {hint ? (
          <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** Signed percentage, e.g. 0.23 -> "+23%", -0.1 -> "-10%", 0 -> "0%". */
function formatSignedPct(value: number): string {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${formatPct(Math.abs(value))}`;
}

function TopAnswers({ answers }: { answers: TopAnswer[] }) {
  if (answers.length === 0) {
    return (
      <p className="py-6 text-sm text-muted-foreground">
        No converting answers yet in this range.
      </p>
    );
  }
  return (
    <ol className="divide-y">
      {answers.map((a) => (
        <li
          key={a.prompt}
          className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm"
        >
          <span className="font-medium">&ldquo;{a.prompt}&rdquo;</span>
          <span className="tabular-nums text-muted-foreground">
            {a.leads} lead{a.leads === 1 ? "" : "s"} →{" "}
            <span className="font-medium text-foreground">
              {formatCurrency(a.value)}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}

function buildCsvRows(pipeline: Pipeline): ExportRow[] {
  return [
    ["Pipeline — headline"],
    ["Pipeline influenced", pipeline.influenced],
    ["Directly attributed", pipeline.attributed],
    ["Leads", pipeline.leads],
    ["Incremental lift (holdout)", pipeline.lift],
    [],
    ["Attribution method", "Value"],
    ...(Object.keys(pipeline.method_breakdown) as (keyof Pipeline["method_breakdown"])[]).map(
      (method) => [METHOD_CSV_LABEL[method] ?? method, pipeline.method_breakdown[method]],
    ),
    [],
    ["How this is measured"],
    [pipeline.confidence_note],
    [],
    ["Top-converting answer", "Leads", "Value"],
    ...pipeline.top_answers.map((a) => [a.prompt, a.leads, a.value]),
  ];
}

function PipelineSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading pipeline">
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
          <Skeleton className="h-4 w-48" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    </div>
  );
}

function EmptyState() {
  return (
    <Card className="mx-auto mt-10 max-w-lg text-center">
      <CardHeader>
        <CardTitle>No pipeline data yet</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Once your brand is set up, the lead-capture pixel is installed, and
          the first measurement snapshot lands, revenue from AI search appears
          here.
        </p>
        <Button asChild>
          <Link href="/onboarding">Start onboarding</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

export default function PipelinePage() {
  const { brandId, range } = useFilters();

  const brandsQuery = useQuery({
    queryKey: ["brands"],
    queryFn: () => apiClient(getToken).brands(),
  });

  const activeBrandId = brandId ?? brandsQuery.data?.[0]?.id ?? null;

  const pipelineQuery = useQuery({
    queryKey: ["pipeline", activeBrandId, range],
    queryFn: () => apiClient(getToken).pipeline(activeBrandId as string, range),
    enabled: activeBrandId !== null,
  });

  if (brandsQuery.isLoading) return <PipelineSkeleton />;

  if (!brandsQuery.data || brandsQuery.data.length === 0 || activeBrandId === null) {
    return <EmptyState />;
  }

  if (pipelineQuery.isLoading || !pipelineQuery.data) return <PipelineSkeleton />;

  const p = pipelineQuery.data;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Pipeline</h1>
          <p className="text-sm text-muted-foreground">
            Revenue from AI search — the screen that justifies the budget.
          </p>
        </div>
        <ExportButton
          filename={`pipeline-${activeBrandId}-${range}`}
          rows={buildCsvRows(p)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard label="Pipeline influenced" value={formatCurrency(p.influenced)} />
        <KpiCard
          label="Directly attributed"
          value={formatCurrency(p.attributed)}
          hint="See the method breakdown below"
        />
        <KpiCard label="Leads" value={String(p.leads)} />
        <KpiCard
          label="Incremental lift"
          value={`${formatSignedPct(p.lift)} (HO)`}
          hint="Measured against a control cohort"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Top-converting AI answers</CardTitle>
        </CardHeader>
        <CardContent>
          <TopAnswers answers={p.top_answers} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Attribution method breakdown</CardTitle>
          <CardDescription>
            Every number above is a mix of methods, not one figure — the
            breakdown and its confidence note always ship together.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <MethodBreakdown
            breakdown={p.method_breakdown}
            confidenceNote={p.confidence_note}
          />
        </CardContent>
      </Card>
    </div>
  );
}
