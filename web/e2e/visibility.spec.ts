import { test, expect } from "@playwright/test";

/**
 * Visibility screen e2e (network fully mocked). Seeds an auth session so the
 * (app) layout guard passes, mocks /brands + the visibility endpoint, then
 * expands a prompt row and asserts the drawer shows sampled-answer counts.
 */

const SESSION = {
  accessToken: "t",
  refreshToken: "r",
  role: "owner",
  tenantId: "t1",
};

const VISIBILITY = {
  engines: [
    {
      engine: "chatgpt",
      mention_rate: 0.42,
      ci: [0.36, 0.48],
      cited: 0.31,
      avg_position: 2.4,
      sentiment: "positive",
      n_samples: 120,
      trend: [
        { date: "2026-06-01", mention_rate: 0.3 },
        { date: "2026-06-15", mention_rate: 0.42 },
      ],
    },
  ],
  prompts: [
    {
      prompt_id: "p1",
      text: "best CRM for startups",
      mention_rate: 0.5,
      avg_position: 2.0,
      n_samples: 24,
    },
  ],
};

test("visibility: expand a prompt row shows sampled-answer counts", async ({
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
  await page.route("**/brands/b1/visibility**", (route) =>
    route.fulfill({ json: VISIBILITY }),
  );

  await page.goto("/visibility");

  // Engine row renders.
  await expect(page.getByText(/chatgpt/i)).toBeVisible();

  // Expand the prompt row → drawer reveals the sampled-answer count.
  await page
    .getByRole("button", { name: /best CRM for startups/i })
    .click();
  await expect(page.getByText(/24 sampled answers/i)).toBeVisible();
});
