import { test, expect, type Page } from "@playwright/test";

import { SEED } from "./fixtures/seed";

/**
 * The M2 happy path, driven against the REAL backend (`create_app` over seeded SQLite via the
 * Playwright webServer + a same-origin Next rewrite — see playwright.config.ts / next.config.mjs).
 * No mocked routes: this exercises the full UI → API path end-to-end.
 */

async function login(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(SEED.t1.email);
  await page.getByLabel("Password").fill(SEED.t1.password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/overview/);
}

test("login → overview → pipeline shows attribution breakdown", async ({ page }) => {
  await login(page);

  // Overview renders the landing KPIs off the seeded snapshots.
  await expect(page.getByText(/share of voice/i)).toBeVisible();

  await page.getByRole("link", { name: /pipeline/i }).click();
  // The method breakdown + the honesty disclosure always ship together (PRD §13). `.first()`
  // because the real confidence note names "holdout"/"the only causal measurement" AND the
  // "How this is measured" heading is present — the disclosure resolving to >1 match is exactly
  // what we want to see, so assert at least one is visible rather than trip strict mode.
  await expect(page.getByText(/holdout/i).first()).toBeVisible(); // method breakdown / note
  await expect(page.getByText(/only causal|how this is measured/i).first()).toBeVisible();
});

test("settings: the lib/api.ts contract fixes work against the real backend (snippet + prompt-add)", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("link", { name: /settings/i }).click();
  await expect(page).toHaveURL(/\/settings/);

  // (a) leadCaptureSnippet(brandId) now sends the required ?brand_id= — the real backend 422s
  //     without it, so a rendered snippet (carrying a data-key) proves the client fix.
  await expect(page.getByText(/data-key=/i)).toBeVisible();

  // (b) savePrompts maps the array to N singular POSTs — add a uniquely-named prompt, confirm it
  //     shows, then reload (refetches GET /brands/{id}/prompts) and confirm it persisted exactly
  //     once: no 422, no duplicate.
  const uniquePrompt = `e2e added prompt ${Date.now()}`;
  await page.getByLabel(/add a prompt/i).fill(uniquePrompt);
  await page.getByRole("button", { name: /^add$/i }).click();
  await expect(page.getByText(uniquePrompt)).toBeVisible();

  await page.reload();
  await expect(page.getByText(uniquePrompt)).toHaveCount(1);
});
