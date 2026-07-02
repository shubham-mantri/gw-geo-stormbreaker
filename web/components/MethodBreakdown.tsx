import type { AttributionMethod, Pipeline } from "@/lib/types";
import { cn, formatCurrency } from "@/lib/utils";

/**
 * Fixed display order for the four attribution methods (m2-design §2 / ui-spec
 * §3.6): strongest → weakest evidence, ending in the one causal mechanism.
 * This is also the fixed categorical color-slot order — identity, never
 * re-sorted by value (dataviz: categorical hues are assigned in a fixed
 * order, never cycled).
 */
const METHOD_ORDER: AttributionMethod[] = [
  "direct",
  "citation_linked",
  "assisted",
  "holdout_incremental",
];

/**
 * User-facing copy for each method. Deliberately avoids the word "holdout" —
 * that term is reserved for the backend-authored `confidence_note` so the
 * note's own wording is never duplicated elsewhere on the screen.
 */
const METHOD_LABEL: Record<AttributionMethod, string> = {
  direct: "Direct referral",
  citation_linked: "Citation-linked",
  assisted: "Assisted",
  holdout_incremental: "Incremental (causal)",
};

/** Categorical palette slots 1/2/3/4 (validated: `dataviz` skill palette). */
const METHOD_COLOR_CSS = `
.method-breakdown { --mb-direct: #2a78d6; --mb-citation_linked: #1baf7a; --mb-assisted: #eda100; --mb-holdout_incremental: #008300; }
.dark .method-breakdown { --mb-direct: #3987e5; --mb-citation_linked: #199e70; --mb-assisted: #c98500; --mb-holdout_incremental: #008300; }
`;

export type MethodBreakdownProps = {
  breakdown: Pipeline["method_breakdown"];
  confidenceNote: string;
  className?: string;
};

/**
 * Renders the attribution-method breakdown (part-to-whole, ui-spec §3.6) as a
 * stacked bar + a labelled value per method, followed by the "how this is
 * measured" confidence disclosure.
 *
 * Non-negotiable: this component ALWAYS renders all four methods — even at
 * zero — and ALWAYS renders `confidenceNote`. A bare attribution number must
 * never appear without this context (PRD §13 / m2-design §1's honesty rule),
 * so nothing here is conditionally hidden behind a toggle or a truthiness
 * check on the values.
 */
export function MethodBreakdown({
  breakdown,
  confidenceNote,
  className,
}: MethodBreakdownProps) {
  const total = METHOD_ORDER.reduce((sum, method) => sum + breakdown[method], 0);
  const barLabel = METHOD_ORDER.map(
    (method) => `${METHOD_LABEL[method]}: ${formatCurrency(breakdown[method])}`,
  ).join(", ");

  return (
    <div className={cn("method-breakdown space-y-4", className)}>
      <style dangerouslySetInnerHTML={{ __html: METHOD_COLOR_CSS }} />

      <div
        role="img"
        aria-label={`Attribution method mix — ${barLabel}`}
        className="flex h-6 w-full gap-0.5 overflow-hidden rounded-full bg-muted"
      >
        {METHOD_ORDER.map((method) => {
          const value = breakdown[method];
          const pct = total > 0 ? (value / total) * 100 : 0;
          if (pct <= 0) return null;
          return (
            <div
              key={method}
              title={`${METHOD_LABEL[method]}: ${formatCurrency(value)} (${pct.toFixed(0)}%)`}
              className="h-full first:rounded-l-full last:rounded-r-full"
              style={{ width: `${pct}%`, backgroundColor: `var(--mb-${method})` }}
            />
          );
        })}
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        {METHOD_ORDER.map((method) => (
          <div key={method} className="flex items-start gap-2">
            <span
              aria-hidden="true"
              className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: `var(--mb-${method})` }}
            />
            <div>
              <dt className="text-xs text-muted-foreground">
                {METHOD_LABEL[method]}
              </dt>
              <dd className="tabular-nums font-medium">
                {formatCurrency(breakdown[method])}
              </dd>
            </div>
          </div>
        ))}
      </dl>

      <div className="rounded-md border border-dashed bg-muted/30 p-3 text-sm">
        <p className="font-medium">How this is measured</p>
        <p className="mt-1 text-muted-foreground">{confidenceNote}</p>
      </div>
    </div>
  );
}
