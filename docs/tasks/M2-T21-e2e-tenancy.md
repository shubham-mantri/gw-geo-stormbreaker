# M2-T21 — E2E happy path + cross-tenant isolation gate

**Depends on:** T18, T19, T20 · **Wave:** 3 (last) · **Suggested agent:** general-purpose
(integration task — assign after the screens land)

**Goal:** The M2 release gate. A full Playwright E2E happy path (login → onboard → Overview →
Visibility → Pipeline) against a **real `create_app()` backend on SQLite** (seeded, no live services),
plus the hard **cross-tenant isolation** test spanning API + UI (TRD §7). Nothing ships if a tenant
can see another tenant's data.

**Files:**
- Create: `web/e2e/happy-path.spec.ts`, `web/e2e/tenancy.spec.ts`,
  `tests/api/test_contract_fidelity.py`, `web/e2e/fixtures/seed.ts`
- Edit: `web/playwright.config.ts` (webServer: start API + `web` against a seeded test DB)

## Interface / behavior
- Playwright `webServer` boots the FastAPI app (`uvicorn gw_geo.handlers.api:handler`-equivalent via
  `create_app`) on a seeded SQLite DB + the Next.js dev server; tests drive the real UI→API path.
- `test_contract_fidelity.py` asserts each M2 endpoint response validates against the ui-spec §6
  schema (the single source of truth the `web/` types mirror).

## Steps
- [ ] **1. Failing tests.** `web/e2e/happy-path.spec.ts`:

```ts
import { test, expect } from "@playwright/test";
test("login → overview → pipeline shows attribution breakdown", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("owner@t1.com");
  await page.getByLabel("Password").fill("pw");
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/overview/);
  await expect(page.getByText(/share of voice/i)).toBeVisible();
  await page.getByRole("link", { name: /pipeline/i }).click();
  await expect(page.getByText(/holdout/i)).toBeVisible();            // method breakdown
  await expect(page.getByText(/only causal|how this is measured/i)).toBeVisible();
});
```
`web/e2e/tenancy.spec.ts`:

```ts
test("tenant B cannot load tenant A's brand", async ({ request }) => {
  const login = await request.post("/auth/login", { data: { email: "owner@t2.com", password: "pw" } });
  const token = (await login.json()).access_token;
  const res = await request.get("/brands/b1/overview", {          // b1 belongs to tenant t1
    headers: { Authorization: `Bearer ${token}` } });
  expect(res.status()).toBe(404);                                  // not 403 — no existence leak
});
```
`tests/api/test_contract_fidelity.py`:

```python
import jsonschema
from tests.api.schemas_uispec import OVERVIEW_SCHEMA, PIPELINE_SCHEMA, VISIBILITY_SCHEMA

def test_overview_matches_uispec(app_client, t1_token, seeded_snapshots):
    body = app_client.get("/brands/b1/overview?range=30d",
        headers={"Authorization": f"Bearer {t1_token}"}).json()
    jsonschema.validate(body, OVERVIEW_SCHEMA)

def test_pipeline_matches_uispec(app_client, t1_token, seeded_full_attribution):
    body = app_client.get("/brands/b1/pipeline?range=90d",
        headers={"Authorization": f"Bearer {t1_token}"}).json()
    jsonschema.validate(body, PIPELINE_SCHEMA)      # includes method_breakdown + confidence_note
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the seed fixtures (2 tenants, brands b1/b2, users, snapshots, attribution
  links), the Playwright `webServer` wiring, and the ui-spec JSON schemas mirrored in
  `tests/api/schemas_uispec.py`. Make the tenancy test the CI gate.
- [ ] **4. Run → pass** (full `pytest` + `web` E2E green).
- [ ] **5. Commit:** `test(m2): e2e happy path + cross-tenant isolation gate`

## Acceptance
- E2E happy path passes against the real API on seeded SQLite (login→overview→pipeline with the
  attribution breakdown + confidence disclosure visible); cross-tenant access returns 404 (no leak) at
  both API and UI; every M2 endpoint validates against the ui-spec §6 schema; the tenancy test gates
  the milestone.
