# M2-T17 — Web scaffold: Next.js app + auth + API client + nav

**Depends on:** T04 · **Wave:** 3 · **Suggested agent:** general-purpose

**Goal:** Stand up the `web/` Next.js dashboard shell (ui-spec §1/§2): App Router, TypeScript, Tailwind
+ shadcn/ui, TanStack Query, a typed API client bound to the ui-spec §6 contract, login, and the
sidebar + top-bar layout (brand switcher, date range, engine filter). Tenant comes from the token —
**never client-set** (ui-spec §5).

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/next.config.mjs`,
  `web/vitest.config.ts`, `web/playwright.config.ts`, `web/tailwind.config.ts`,
  `web/app/(auth)/login/page.tsx`, `web/app/(app)/layout.tsx`,
  `web/lib/api.ts`, `web/lib/auth.ts`, `web/lib/types.ts`,
  `web/components/TopBar.tsx`, `web/components/Sidebar.tsx`, `web/components/ConfidenceBadge.tsx`
- Test: `web/lib/api.test.ts`, `web/components/ConfidenceBadge.test.tsx`,
  `web/e2e/login.spec.ts`

## Interface

```ts
// lib/types.ts — mirror ui-spec §6 shapes
export type Brand = { id: string; name: string; domain: string; competitors: string[] };
export type Overview = { sov: number; mention_rate: number; pipeline: number; leads: number;
                         trend: { date: string; you: number; competitor: number }[] };
export type EngineRow = { engine: string; mention_rate: number; ci: [number, number];
                          cited: number; avg_position: number | null; sentiment: string; n_samples: number;
                          trend: { date: string; mention_rate: number }[] };
export type Pipeline = { influenced: number; attributed: number; leads: number; lift: number;
                         top_answers: { prompt: string; leads: number; value: number }[];
                         method_breakdown: Record<"direct"|"citation_linked"|"assisted"|"holdout_incremental", number>;
                         confidence_note: string };

// lib/api.ts
export function apiClient(getToken: () => string | null): {
  brands(): Promise<Brand[]>;
  overview(brandId: string, range: string): Promise<Overview>;
  visibility(brandId: string, q: {range?:string; geo?:string; persona?:string}): Promise<{engines: EngineRow[]; prompts: unknown[]}>;
  sources(brandId: string, range: string): Promise<unknown[]>;
  pipeline(brandId: string, range: string): Promise<Pipeline>;
  alerts(brandId: string): Promise<{severity:string; message:string; ts:string}[]>;
};
```
Every request sends `Authorization: Bearer <token>`; 401 → redirect to `/login`.

## Steps
- [ ] **1. Failing tests.** `web/lib/api.test.ts` (Vitest, mocked `fetch`):

```ts
import { describe, it, expect, vi } from "vitest";
import { apiClient } from "./api";
describe("apiClient", () => {
  it("sends bearer token and parses brands", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([{ id: "b1", name: "Acme", domain: "acme.com", competitors: [] }])));
    vi.stubGlobal("fetch", fetchMock);
    const api = apiClient(() => "tok123");
    const brands = await api.brands();
    expect(brands[0].id).toBe("b1");
    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string,string>;
    expect(headers.Authorization).toBe("Bearer tok123");
  });
});
```
`web/components/ConfidenceBadge.test.tsx` (RTL):

```tsx
import { render, screen } from "@testing-library/react";
import { ConfidenceBadge } from "./ConfidenceBadge";
it("shows CI and sample size", () => {
  render(<ConfidenceBadge value={0.42} ci={[0.36, 0.48]} n={120} />);
  expect(screen.getByText(/42%/)).toBeInTheDocument();
  expect(screen.getByText(/±/)).toBeInTheDocument();
  expect(screen.getByText(/n=120/)).toBeInTheDocument();
});
```
`web/e2e/login.spec.ts` (Playwright, mocked API route):

```ts
import { test, expect } from "@playwright/test";
test("login redirects to overview", async ({ page }) => {
  await page.route("**/auth/login", (r) =>
    r.fulfill({ json: { access_token: "t", refresh_token: "r", role: "owner", tenant_id: "t1" } }));
  await page.goto("/login");
  await page.getByLabel("Email").fill("u@x.com");
  await page.getByLabel("Password").fill("pw");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/overview/);
});
```

- [ ] **2. Run → fail** (`npm --prefix web test`, `npx playwright test`).
- [ ] **3. Implement** the scaffold: Next.js config, Tailwind/shadcn setup, `apiClient` (bearer +
  401→login), `lib/auth.ts` (token store, tenant read-only from token), the app layout with
  `Sidebar` (Overview/Visibility/Sources/Pipeline/Alerts/Settings) + `TopBar` (brand switcher, date
  range, engine filter), and `ConfidenceBadge`. Add a `web` CI job (lint/typecheck/vitest/playwright).
- [ ] **4. Run → pass**; `tsc --noEmit` clean.
- [ ] **5. Commit:** `feat(web): next.js scaffold + auth + api client + nav shell`

## Acceptance
- `web/` builds; `apiClient` sends bearer + parses contract shapes; `ConfidenceBadge` renders CI +
  `n`; login flow redirects to Overview (mocked API); sidebar lists the 6 M2 screens; tenant is never
  client-selectable; Vitest + Playwright green.
