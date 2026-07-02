import { test, expect } from "@playwright/test";

/**
 * Onboarding wizard e2e (network fully mocked, ui-spec §4): walks the 5-step wizard
 * (brand -> competitors -> integrations -> seed prompts -> "measuring…"), confirms it lands on the
 * measuring state, then follows the "Go to Overview" CTA through to the dashboard.
 */

const SESSION = {
  accessToken: "t",
  refreshToken: "r",
  role: "owner",
  tenantId: "t1",
};

test("onboarding: completes the 5-step wizard and lands on measuring, then continues to Overview", async ({
  page,
}) => {
  await page.addInitScript((session) => {
    window.localStorage.setItem("gw_geo_session", JSON.stringify(session));
  }, SESSION);

  // `POST /brands` (create, from the wizard) and `GET /brands` (list, from the dashboard shell once
  // we land on Overview) share a path — branch on method.
  await page.route("**/brands", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ json: { id: "b1" } });
    }
    return route.fulfill({
      json: [{ id: "b1", name: "Acme", domain: "acme.com", competitors: ["Beta"] }],
    });
  });
  await page.route("**/brands/b1/prompts", (route) =>
    route.fulfill({
      json: [
        { id: "seed-0", text: "best CRM for startups", intent_cluster: "", geo: "us", persona: "" },
      ],
    }),
  );
  await page.route("**/integrations/hubspot", (route) =>
    route.fulfill({ json: { status: "connected" } }),
  );
  await page.route("**/brands/b1/overview**", (route) =>
    route.fulfill({ json: { sov: 0, mention_rate: 0, pipeline: 0, leads: 0, trend: [] } }),
  );
  await page.route("**/brands/b1/alerts**", (route) => route.fulfill({ json: [] }));

  await page.goto("/onboarding");

  // Step 1 — brand.
  await expect(page.getByText(/step 1 of 5/i)).toBeVisible();
  await page.getByLabel(/brand name/i).fill("Acme");
  await page.getByLabel(/domain/i).fill("acme.com");
  await page.getByRole("button", { name: /^next$/i }).click();

  // Step 2 — competitors.
  await expect(page.getByText(/step 2 of 5/i)).toBeVisible();
  await page.getByLabel(/add a competitor/i).fill("Beta");
  await page.getByRole("button", { name: /^add$/i }).click();
  await expect(page.getByText("Beta")).toBeVisible();
  await page.getByRole("button", { name: /^next$/i }).click();

  // Step 3 — integrations.
  await expect(page.getByText(/step 3 of 5/i)).toBeVisible();
  await page.getByRole("button", { name: /connect hubspot/i }).click();
  await expect(page.getByRole("button", { name: /^connected$/i })).toBeVisible();
  await page.getByRole("button", { name: /^next$/i }).click();

  // Step 4 — seed prompts.
  await expect(page.getByText(/step 4 of 5/i)).toBeVisible();
  await page.getByLabel(/add a prompt/i).fill("best CRM for startups");
  await page.getByRole("button", { name: /^add$/i }).click();
  await page.getByRole("button", { name: /start measuring/i }).click();

  // Step 5 — measuring.
  await expect(page.getByText(/step 5 of 5/i)).toBeVisible();
  await expect(page.getByText(/check back/i)).toBeVisible();

  // Continues on to Overview.
  await page.getByRole("button", { name: /go to overview/i }).click();
  await expect(page).toHaveURL(/\/overview/);
});
