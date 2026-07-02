"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { OverviewTrendPoint } from "@/lib/types";
import { formatPct } from "@/lib/utils";

/**
 * Share-of-Voice trend: you vs your top competitor, over the selected range.
 *
 * Two categorical series (identity, not magnitude) → validated CVD-safe pair
 * (blue `You` / orange competitor, worst-adjacent ΔE ≈ 97), a legend is always
 * present, grid + axes are recessive, and a crosshair tooltip is on by default.
 */

/**
 * Series colors as scoped CSS vars so the light/dark values swap in one place.
 * The app toggles dark mode via a `.dark` class (Tailwind `darkMode: "class"`).
 */
const CHART_CSS = `
.sov-trend { --sov-you: #2a78d6; --sov-competitor: #eb6834; }
.dark .sov-trend { --sov-you: #3987e5; --sov-competitor: #d95926; }
`;

function formatDate(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export type SoVTrendProps = {
  data: OverviewTrendPoint[];
  /** Label for the competitor series (defaults to a generic label). */
  competitorLabel?: string;
  height?: number;
};

export function SoVTrend({
  data,
  competitorLabel = "Top competitor",
  height = 260,
}: SoVTrendProps) {
  if (!data || data.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground"
        style={{ height }}
      >
        No trend data yet — it appears once measurement runs.
      </div>
    );
  }

  return (
    <div className="sov-trend w-full" style={{ height }}>
      <style dangerouslySetInnerHTML={{ __html: CHART_CSS }} />
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 8, right: 16, bottom: 4, left: 0 }}
        >
          <CartesianGrid
            vertical={false}
            stroke="hsl(var(--border))"
            strokeDasharray="3 3"
          />
          <XAxis
            dataKey="date"
            tickFormatter={formatDate}
            tickLine={false}
            axisLine={{ stroke: "hsl(var(--border))" }}
            tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }}
            minTickGap={24}
          />
          <YAxis
            tickFormatter={(v: number) => formatPct(v)}
            tickLine={false}
            axisLine={false}
            width={44}
            tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }}
            domain={[0, "auto"]}
          />
          <Tooltip
            formatter={(value: number, name) => [formatPct(value), name]}
            labelFormatter={formatDate}
            contentStyle={{
              borderRadius: 8,
              border: "1px solid hsl(var(--border))",
              background: "hsl(var(--popover))",
              color: "hsl(var(--popover-foreground))",
              fontSize: 12,
            }}
          />
          <Legend verticalAlign="top" height={28} iconType="plainline" />
          <Line
            type="monotone"
            name="You"
            dataKey="you"
            stroke="var(--sov-you)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            name={competitorLabel}
            dataKey="competitor"
            stroke="var(--sov-competitor)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
