import { test, expect } from "@playwright/test";

import { SEED } from "./fixtures/seed";

/**
 * The M2 release gate: cross-tenant isolation (TRD §7). Tenant B, holding a perfectly valid token,
 * must NOT be able to load tenant A's brand — and the refusal must be a **404, not a 403**, so the
 * mere existence of another tenant's brand is never confirmed (no existence leak).
 *
 * Driven against the real backend via the same-origin API proxy (baseURL is the web server, which
 * rewrites API paths to the seeded FastAPI app). Nothing ships if this fails.
 */
test("tenant B cannot load tenant A's brand", async ({ request }) => {
  const login = await request.post("/auth/login", {
    data: { email: SEED.t2.email, password: SEED.t2.password },
  });
  expect(login.ok()).toBeTruthy();
  const token = (await login.json()).access_token as string;

  // b1 belongs to tenant t1; t2's token must not reach it.
  const res = await request.get(`/brands/${SEED.foreignBrandForT2}/overview`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(res.status()).toBe(404); // not 403 — no existence leak
});
