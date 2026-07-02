# M2-T19 — Screens: Sources + Pipeline

**Depends on:** T17, T14, T15 · **Wave:** 3 · **Suggested agent:** general-purpose

**Goal:** Build the Sources (ui-spec §3.3) and Pipeline (§3.6 — ★ the payoff) screens. Pipeline MUST
render the **attribution-method breakdown + confidence disclosure** ("how this is measured") — honesty
≠ overclaim (m2-design §1).

**Files:**
- Create: `web/app/(app)/sources/page.tsx`, `web/app/(app)/pipeline/page.tsx`,
  `web/components/SourceMap.tsx`, `web/components/MethodBreakdown.tsx`,
  `web/components/ExportButton.tsx`
- Test: `web/app/(app)/sources/sources.test.tsx`, `web/app/(app)/pipeline/pipeline.test.tsx`,
  `web/e2e/pipeline.spec.ts`

## Interface / behavior
- **Sources:** table of citation domains (`source_type`, `you_pct`, competitor pcts) with gap
  highlighting + per-engine toggle. Data: `api.sources(brandId, range)`.
- **Pipeline:** headline cards (Influenced, Directly attributed, Leads, Incremental lift +HO),
  top-converting answers, a **`MethodBreakdown`** component (direct / citation-linked / assisted /
  holdout-incremental) + the `confidence_note` disclosure, and an exec/board **export** (PDF/CSV).
  Data: `api.pipeline(brandId, range)`.

## Steps
- [ ] **1. Failing tests.** `web/app/(app)/pipeline/pipeline.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import PipelinePage from "./page";
import { renderWithClient, mockApi } from "@/test/utils";
it("renders method breakdown and confidence note", async () => {
  mockApi({ pipeline: { influenced: 480000, attributed: 92000, leads: 137, lift: 0.23,
    top_answers: [{ prompt: "best CRM for SaaS startups", leads: 41, value: 210000 }],
    method_breakdown: { direct: 40000, citation_linked: 52000, assisted: 300000, holdout_incremental: 88000 },
    confidence_note: "Holdout incrementality is the only causal figure; others are correlational." } });
  renderWithClient(<PipelinePage />);
  expect(await screen.findByText(/holdout/i)).toBeInTheDocument();          // breakdown key
  expect(await screen.findByText(/only causal/i)).toBeInTheDocument();      // confidence note shown
  expect(await screen.findByText(/\$92,?000/)).toBeInTheDocument();         // attributed
});
```
`web/app/(app)/sources/sources.test.tsx`:

```tsx
it("flags competitor gaps", async () => {
  mockApi({ sources: [{ domain:"reddit.com", source_type:"reddit", you_pct:0.48,
    competitor_pcts:{ Beta: 0.71 } }] });
  renderWithClient(<SourcesPage />);
  expect(await screen.findByText("reddit.com")).toBeInTheDocument();
  expect(await screen.findByText(/gap/i)).toBeInTheDocument();
});
```
`web/e2e/pipeline.spec.ts` (Playwright): loads Pipeline, asserts the "how this is measured" disclosure
and an export button are present.

- [ ] **2. Run → fail.**
- [ ] **3. Implement** both pages + components; the `MethodBreakdown` must always render all four
  methods and the `confidence_note` (never hide the caveat); export produces CSV client-side + a PDF
  hook.
- [ ] **4. Run → pass** (Vitest + Playwright); `tsc --noEmit` clean.
- [ ] **5. Commit:** `feat(web): sources + pipeline screens`

## Acceptance
- Sources highlights competitor gaps; Pipeline shows headline cards, top answers, the full
  four-method breakdown, the `confidence_note` disclosure, and an export control; a bare attribution
  number is never shown without its method mix; tests green.
