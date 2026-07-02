import { test, expect } from "@playwright/test";

/**
 * Pipeline screen e2e (network fully mocked). Seeds an auth session so the
 * (app) layout guard passes, mocks /brands + the pipeline endpoint, then
 * asserts the "how this is measured" disclosure and an export control are
 * present — the honesty-over-overclaim rule (ui-spec §3.6 / m2-design §1) is
 * the whole point of this screen, so it must never be behind an extra click.
 */

const SESSION = {
  accessToken: "t",
  refreshToken: "r",
  role: "owner",
  tenantId: "t1",
};

const PIPELINE = {
  influenced: 480000,
  attributed: 92000,
  leads: 137,
  lift: 0.23,
  top_answers: [
    { prompt: "best CRM for SaaS startups", leads: 41, value: 210000 },
    { prompt: "HubSpot alternatives", leads: 28, value: 88000 },
  ],
  method_breakdown: {
    direct: 40000,
    citation_linked: 52000,
    assisted: 300000,
    holdout_incremental: 88000,
  },
  confidence_note:
    "Holdout incrementality is the only causal figure; others are correlational.",
};

test("pipeline: shows the method breakdown, its confidence disclosure, and an export control", async ({
  page,
}) => {
  await page.addInitScript((session) => {
    window.localStorage.setItem("gw_geo_session", JSON.stringify(session));
  }, SESSION);

  await page.route("**/brands", (route) =>
    route.fulfill({
      json: [
        { id: "b1", name: "Acme", domain: "acme.com", competitors: ["Beta"] },
      ],
    }),
  );
  await page.route("**/brands/b1/pipeline**", (route) =>
    route.fulfill({ json: PIPELINE }),
  );

  await page.goto("/pipeline");

  // Headline number renders...
  await expect(page.getByText(/\$92,?000/).first()).toBeVisible();

  // ...and never without the method mix + the disclosure right there with it.
  await expect(page.getByText(/attribution method breakdown/i)).toBeVisible();
  await expect(page.getByText(/how this is measured/i)).toBeVisible();
  await expect(page.getByText(/only causal/i)).toBeVisible();

  // The exec/board export control is present.
  await expect(
    page.getByRole("button", { name: /export csv/i }),
  ).toBeVisible();
});
