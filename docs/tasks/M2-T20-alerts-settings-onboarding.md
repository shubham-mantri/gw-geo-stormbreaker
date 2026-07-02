# M2-T20 — Screens: Alerts + Settings + onboarding wizard

**Depends on:** T17, T15, T16 · **Wave:** 3 · **Suggested agent:** general-purpose

**Goal:** Build Alerts (ui-spec §3.7), Settings (§3.8: brands, competitors, prompts, integrations,
lead-capture snippet, team/roles, SSO stub) and the first-run **onboarding wizard** (§4:
brand → competitors → integrations → seed prompts → "measuring… check back").

**Files:**
- Create: `web/app/(app)/alerts/page.tsx`, `web/app/(app)/settings/page.tsx`,
  `web/app/onboarding/page.tsx`,
  `web/components/settings/PromptManager.tsx`, `web/components/settings/IntegrationsPanel.tsx`,
  `web/components/settings/SnippetInstall.tsx`, `web/components/OnboardingWizard.tsx`
- Test: `web/app/(app)/alerts/alerts.test.tsx`, `web/app/(app)/settings/settings.test.tsx`,
  `web/components/OnboardingWizard.test.tsx`, `web/e2e/onboarding.spec.ts`

## Interface / behavior
- **Alerts:** severity-coloured feed from `api.alerts(brandId)` (red/green/yellow).
- **Settings:** `PromptManager` (list/add/prioritize via `/brands/{id}/prompts`),
  `IntegrationsPanel` (connect HubSpot/Salesforce/GA4 via `/integrations/{kind}`),
  `SnippetInstall` (fetch + copy `/lead-capture/snippet`), team/roles table + SSO stub.
- **Onboarding wizard:** 5-step state machine; final step shows the "measuring…" empty state until the
  first snapshot lands, then routes to Overview.

## Steps
- [ ] **1. Failing tests.** `web/components/OnboardingWizard.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { OnboardingWizard } from "./OnboardingWizard";
it("advances through the 5 steps to measuring state", async () => {
  render(<OnboardingWizard />);
  expect(screen.getByText(/step 1 of 5/i)).toBeInTheDocument();      // brand
  fireEvent.change(screen.getByLabelText(/brand name/i), { target: { value: "Acme" } });
  fireEvent.change(screen.getByLabelText(/domain/i), { target: { value: "acme.com" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));    // -> competitors
  expect(screen.getByText(/step 2 of 5/i)).toBeInTheDocument();
});
```
`web/app/(app)/alerts/alerts.test.tsx`:

```tsx
it("colours alerts by severity", async () => {
  mockApi({ alerts: [{ severity:"red", message:"ChatGPT visibility -8%", ts:"2026-06-30T00:00:00Z" }] });
  renderWithClient(<AlertsPage />);
  const item = await screen.findByText(/ChatGPT visibility/);
  expect(item.closest("[data-severity='red']")).not.toBeNull();
});
```
`web/app/(app)/settings/settings.test.tsx`: `SnippetInstall` renders the fetched snippet and a copy
button; `IntegrationsPanel` shows connect buttons for hubspot/salesforce/ga4.
`web/e2e/onboarding.spec.ts` (Playwright, routes mocked): completes the wizard, lands on "measuring…".

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the three surfaces; approval/role gates reflected in the UI (viewer can't
  connect integrations); SSO row is a labelled stub. Wire the wizard writes to `POST /brands` +
  `/brands/{id}/prompts` + `/integrations/{kind}`.
- [ ] **4. Run → pass** (Vitest + Playwright); `tsc --noEmit` clean.
- [ ] **5. Commit:** `feat(web): alerts + settings + onboarding wizard`

## Acceptance
- Alerts colour by severity; Settings manages prompts, connects integrations, and shows the copyable
  install snippet; onboarding wizard runs the 5-step flow to the "measuring…" state; role gates
  respected in UI; tests green.
