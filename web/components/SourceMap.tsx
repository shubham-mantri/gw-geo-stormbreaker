"use client";

import { useState } from "react";
import { TriangleAlert } from "lucide-react";

import type { Source } from "@/lib/types";
import { cn, formatPct } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const ENGINE_VIEWS = [
  { value: "all", label: "All engines" },
  { value: "chatgpt", label: "ChatGPT" },
  { value: "perplexity", label: "Perplexity" },
  { value: "google_ai_overview", label: "Google AI Overview" },
  { value: "gemini", label: "Gemini" },
  { value: "claude", label: "Claude" },
  { value: "copilot", label: "Copilot" },
];

const SOURCE_TYPE_LABELS: Record<string, string> = {
  reddit: "Reddit",
  review_site: "Review site",
  own_site: "Owned site",
  wikipedia: "Wikipedia",
  forum: "Forum",
  news: "News",
  social: "Social",
};

function sourceTypeLabel(type: string): string {
  return SOURCE_TYPE_LABELS[type] ?? type;
}

/** A competitor cell is a gap when they're cited more often than you are. */
function isGap(youPct: number, competitorPct: number): boolean {
  return competitorPct > youPct;
}

export type SourceMapProps = {
  sources: Source[];
  className?: string;
};

/**
 * Citation-source map (ui-spec §3.3): which domains AI engines cite, how
 * often they cite you vs. each competitor, and where a competitor is cited
 * more than you — a seeding opportunity, highlighted inline and on the row.
 *
 * The engine toggle is local view state. The `GET /brands/{id}/sources`
 * contract (ui-spec §6) returns one engine-blended figure per source today —
 * there is no per-engine dimension to slice yet — so switching engines here
 * re-labels the view and says so plainly rather than silently pretending to
 * re-slice data the API doesn't provide. Same honesty rule as the Pipeline
 * method breakdown: never overclaim precision you don't have.
 */
export function SourceMap({ sources, className }: SourceMapProps) {
  const [engineView, setEngineView] = useState<string>("all");

  const competitors = Array.from(
    new Set(sources.flatMap((s) => Object.keys(s.competitor_pcts))),
  ).sort();

  const engineViewLabel =
    ENGINE_VIEWS.find((e) => e.value === engineView)?.label ?? engineView;

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {sources.length} citation source{sources.length === 1 ? "" : "s"}{" "}
          tracked
        </p>
        <Select value={engineView} onValueChange={setEngineView}>
          <SelectTrigger className="w-[190px]" aria-label="Per-engine breakdown">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ENGINE_VIEWS.map((e) => (
              <SelectItem key={e.value} value={e.value}>
                {e.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {engineView !== "all" ? (
        <p className="text-xs italic text-muted-foreground">
          Citation share isn&apos;t split by engine yet — showing the blended
          share across engines for {engineViewLabel}.
        </p>
      ) : null}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Source</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Cites → You</TableHead>
            {competitors.map((c) => (
              <TableHead key={c}>Cites → {c}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sources.map((source) => {
            const rowHasGap = competitors.some(
              (c) =>
                c in source.competitor_pcts &&
                isGap(source.you_pct, source.competitor_pcts[c]),
            );
            return (
              <TableRow
                key={source.domain}
                className={cn(rowHasGap && "bg-destructive/5 hover:bg-destructive/10")}
              >
                <TableCell className="font-medium">{source.domain}</TableCell>
                <TableCell className="text-muted-foreground">
                  {sourceTypeLabel(source.source_type)}
                </TableCell>
                <TableCell className="tabular-nums">
                  {formatPct(source.you_pct)}
                </TableCell>
                {competitors.map((c) => {
                  const present = c in source.competitor_pcts;
                  const pct = source.competitor_pcts[c];
                  const gap = present && isGap(source.you_pct, pct);
                  return (
                    <TableCell key={c} className="tabular-nums">
                      {!present ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        <span className="inline-flex items-center gap-1.5">
                          {formatPct(pct)}
                          {gap ? (
                            <Badge
                              variant="destructive"
                              className="gap-1 px-1.5 py-0 text-[10px] font-medium"
                            >
                              <TriangleAlert className="h-3 w-3" aria-hidden="true" />
                              Gap
                            </Badge>
                          ) : null}
                        </span>
                      )}
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
