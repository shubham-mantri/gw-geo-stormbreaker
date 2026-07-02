/**
 * Shared seed constants for the real-backend E2E specs — the canonical, single-source view of what
 * `tests/e2e_backend.py` (the seeded `create_app` entrypoint Playwright's `webServer` boots over
 * SQLite, no live services) creates. Keep in lockstep with that Python seeder.
 *
 * Two tenants, each with an owner and a brand. Brand `b1` belongs to `t1` ONLY — the cross-tenant
 * isolation gate (tenancy.spec.ts) logs in as `t2` and must get a 404 (not a 403) for `b1`.
 */
export const SEED = {
  t1: {
    email: "owner@t1.com",
    password: "pw",
    tenantId: "t1",
    brandId: "b1",
    brandName: "Acme",
  },
  t2: {
    email: "owner@t2.com",
    password: "pw",
    tenantId: "t2",
    brandId: "b2",
  },
  /** A brand owned by t1 — used to probe cross-tenant access as t2. */
  foreignBrandForT2: "b1",
} as const;
