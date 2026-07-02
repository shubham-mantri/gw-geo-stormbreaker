import { cn, formatPct } from "@/lib/utils";

export type ConfidenceBadgeProps = {
  /** Point estimate as a 0..1 rate. */
  value: number;
  /** 95% confidence interval [low, high] as 0..1 rates. */
  ci: [number, number];
  /** Sample size. */
  n: number;
  className?: string;
};

/**
 * Renders a rate with its confidence interval and sample size — non-determinism
 * is always visible, never hidden (ui-spec §4 / TRD §3). E.g. `42%  ±6%  n=120`.
 */
export function ConfidenceBadge({ value, ci, n, className }: ConfidenceBadgeProps) {
  const halfWidth = (ci[1] - ci[0]) / 2;
  return (
    <span
      className={cn(
        "inline-flex items-baseline gap-1 whitespace-nowrap text-sm",
        className,
      )}
      title={`95% CI ${formatPct(ci[0])}–${formatPct(ci[1])}, n=${n}`}
    >
      <span className="font-medium tabular-nums">{formatPct(value)}</span>
      <span className="tabular-nums text-muted-foreground">±{formatPct(halfWidth)}</span>
      <span className="tabular-nums text-muted-foreground">n={n}</span>
    </span>
  );
}
