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
