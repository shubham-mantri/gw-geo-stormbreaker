# M2-T18 — Screens: Overview + Visibility

**Depends on:** T17, T13, T14 · **Wave:** 3 · **Suggested agent:** general-purpose

**Goal:** Build the Overview (ui-spec §3.1) and Visibility (§3.2) screens against the T13/T14
endpoints. Confidence is always visible (`ConfidenceBadge` on every rate); skeleton loaders; date-range
+ engine filterable.

**Files:**
- Create: `web/app/(app)/overview/page.tsx`, `web/app/(app)/visibility/page.tsx`,
  `web/components/charts/SoVTrend.tsx`, `web/components/EngineTable.tsx`,
  `web/components/PromptDrawer.tsx`
- Test: `web/app/(app)/overview/overview.test.tsx`, `web/app/(app)/visibility/visibility.test.tsx`,
  `web/e2e/visibility.spec.ts`

## Interface / behavior
- **Overview:** 4 KPI cards (SoV, Mention Rate, AI Pipeline, Leads) + a SoV trend line (you vs top
  competitor, Recharts) + a strip of alert/opportunity/win counts. Data: `api.overview(brandId, range)`.
- **Visibility:** engine table (Mention ±CI, Cited, Avg Pos, Sentiment, Trend sparkline) with an
  expandable **prompt-level** drawer. Data: `api.visibility(brandId, {range, geo, persona})`. Every
  metric row shows CI + `n_samples`.

## Steps
- [ ] **1. Failing tests.** `web/app/(app)/overview/overview.test.tsx` (RTL + mocked hook):

```tsx
import { render, screen } from "@testing-library/react";
import OverviewPage from "./page";
import { renderWithClient, mockApi } from "@/test/utils";
it("renders KPI cards from overview data", async () => {
  mockApi({ overview: { sov: 0.19, mention_rate: 0.38, pipeline: 480000, leads: 137, trend: [] } });
  renderWithClient(<OverviewPage />);
  expect(await screen.findByText("19%")).toBeInTheDocument();     // SoV
  expect(await screen.findByText(/\$480,?000|\$480k/)).toBeInTheDocument();
  expect(await screen.findByText("137")).toBeInTheDocument();     // leads
});
```
`web/app/(app)/visibility/visibility.test.tsx`:

```tsx
it("shows per-engine confidence intervals", async () => {
  mockApi({ visibility: { engines: [{ engine:"chatgpt", mention_rate:0.42, ci:[0.36,0.48],
    cited:0.31, avg_position:2.4, sentiment:"positive", n_samples:120, trend:[] }], prompts: [] } });
  renderWithClient(<VisibilityPage />);
  expect(await screen.findByText(/chatgpt/i)).toBeInTheDocument();
  expect(await screen.findByText(/n=120/)).toBeInTheDocument();   // ConfidenceBadge
});
```
`web/e2e/visibility.spec.ts` (Playwright, routes mocked): loads Visibility, expands a prompt row,
asserts the drawer shows sampled-answer counts.

- [ ] **2. Run → fail.**
- [ ] **3. Implement** both pages + components; skeletons while loading; empty-state prompt to
  onboarding when no snapshot exists; wire date-range/engine filters from `TopBar`.
- [ ] **4. Run → pass** (Vitest + Playwright); `tsc --noEmit` clean.
- [ ] **5. Commit:** `feat(web): overview + visibility screens`

## Acceptance
- Overview shows the 4 KPIs + SoV trend from live contract data; Visibility shows per-engine rows each
  with CI + `n_samples` and an expandable prompt drawer; loading = skeletons; filters work; tests
  green.
