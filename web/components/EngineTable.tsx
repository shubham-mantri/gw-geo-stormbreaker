"use client";

import { Line, LineChart } from "recharts";

import type { EngineRow } from "@/lib/types";
import { cn, formatPct } from "@/lib/utils";
import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Human-friendly engine labels; unknown keys fall back to the raw value. */
const ENGINE_LABELS: Record<string, string> = {
  chatgpt: "ChatGPT",
  perplexity: "Perplexity",
  google_ai_overview: "Google AI Overview",
  gemini: "Gemini",
  claude: "Claude",
  copilot: "Copilot",
  grok: "Grok",
  deepseek: "DeepSeek",
};

function engineLabel(engine: string): string {
  return ENGINE_LABELS[engine] ?? engine;
}

/** Map a sentiment label to an emoji + accessible text. */
const SENTIMENT: Record<string, { emoji: string; label: string }> = {
  positive: { emoji: "🙂", label: "Positive" },
  neutral: { emoji: "😐", label: "Neutral" },
  negative: { emoji: "🙁", label: "Negative" },
};

function Sentiment({ sentiment }: { sentiment: string }) {
  const s = SENTIMENT[sentiment] ?? { emoji: "•", label: sentiment };
  return (
    <span title={s.label}>
      <span aria-hidden="true">{s.emoji}</span>
      <span className="sr-only">{s.label}</span>
    </span>
  );
}

function Sparkline({ data }: { data: { date: string; mention_rate: number }[] }) {
  if (!data || data.length < 2) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <LineChart width={96} height={28} data={data} className="overflow-visible">
      <Line
        type="monotone"
        dataKey="mention_rate"
        stroke="hsl(var(--primary))"
        strokeWidth={1.5}
        dot={false}
        isAnimationActive={false}
      />
    </LineChart>
  );
}

export type EngineTableProps = {
  engines: EngineRow[];
  className?: string;
};

/**
 * Per-engine visibility table. Every row's mention rate is rendered as a
 * `ConfidenceBadge` (rate ± CI, `n=` sample size) so non-determinism is always
 * visible (ui-spec §3.2 / §4). Cited %, average position, sentiment and a
 * mention-rate sparkline round out the row.
 */
export function EngineTable({ engines, className }: EngineTableProps) {
  return (
    <Table className={cn(className)}>
      <TableHeader>
        <TableRow>
          <TableHead>Engine</TableHead>
          <TableHead>Mention</TableHead>
          <TableHead>Cited</TableHead>
          <TableHead>Avg Pos</TableHead>
          <TableHead>Sentiment</TableHead>
          <TableHead>Trend</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {engines.map((row) => (
          <TableRow key={row.engine}>
            <TableCell className="font-medium">
              {engineLabel(row.engine)}
            </TableCell>
            <TableCell>
              <ConfidenceBadge
                value={row.mention_rate}
                ci={row.ci}
                n={row.n_samples}
              />
            </TableCell>
            <TableCell className="tabular-nums">
              {formatPct(row.cited)}
            </TableCell>
            <TableCell className="tabular-nums">
              {row.avg_position === null ? "—" : row.avg_position.toFixed(1)}
            </TableCell>
            <TableCell>
              <Sentiment sentiment={row.sentiment} />
            </TableCell>
            <TableCell>
              <Sparkline data={row.trend} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
