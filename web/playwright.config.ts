import { existsSync } from "node:fs";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright e2e config.
 *
 * Boots THREE servers via `webServer`:
 *   1. the REAL FastAPI backend (`tests.e2e_backend:app` = `create_app` over a seeded SQLite DB,
 *      no live services), served by uvicorn from the shared venv;
 *   2. a Next.js dev server WITH `API_PROXY_TARGET` set, so a same-origin rewrite proxies every API
 *      path to (1) — the browser is never cross-origin, so CORS never blocks the E2E. The
 *      `real` project (happy-path + tenancy) runs against this one, driving the full UI → API path.
 *   3. a Next.js dev server WITHOUT the proxy, identical to the app's normal dev mode. The `mocked`
 *      project (the 4 pre-existing specs) runs against this one. Those specs mock the network at the
 *      browser layer (`page.route`); their *unmocked* requests must 404 on Next (as they always
 *      have) — NOT hit a live API and 401 — so they stay green, byte-for-byte unchanged.
 *
 * Dedicated ports (override with WEB_E2E_PORT / WEB_E2E_MOCK_PORT / WEB_E2E_API_PORT).
 */
const PORT = Number(process.env.WEB_E2E_PORT ?? 4488);
const MOCK_PORT = Number(process.env.WEB_E2E_MOCK_PORT ?? 4489);
const API_PORT = Number(process.env.WEB_E2E_API_PORT ?? 8788);
const BASE_URL = `http://localhost:${PORT}`;
const MOCK_URL = `http://localhost:${MOCK_PORT}`;
const API_URL = `http://127.0.0.1:${API_PORT}`;

// This config lives in `web/`; the repo root holds `src/` + `tests/` (the backend + e2e entrypoint).
const REPO_ROOT = path.resolve(__dirname, "..");

/**
 * Locate the shared Python venv interpreter. In a git worktree the venv lives in the main checkout
 * root (an ancestor of this worktree), so walk up a few levels. Override with GEO_E2E_PYTHON.
 */
function findVenvPython(): string {
  if (process.env.GEO_E2E_PYTHON) return process.env.GEO_E2E_PYTHON;
  let dir = REPO_ROOT;
  for (let i = 0; i < 6; i += 1) {
    const candidate = path.join(dir, ".venv", "bin", "python");
    if (existsSync(candidate)) return candidate;
    dir = path.dirname(dir);
  }
  return "python3";
}

const PYTHON = findVenvPython();

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  use: { trace: "on-first-retry" },
  projects: [
    {
      // The 4 pre-existing, fully-mocked specs — run against the plain (proxy-less) Next server.
      name: "mocked",
      testMatch: [
        "**/login.spec.ts",
        "**/pipeline.spec.ts",
        "**/visibility.spec.ts",
        "**/onboarding.spec.ts",
      ],
      use: { ...devices["Desktop Chrome"], baseURL: MOCK_URL },
    },
    {
      // The real-backend specs — run against the proxied Next server (same-origin → real API).
      name: "real",
      testMatch: ["**/happy-path.spec.ts", "**/tenancy.spec.ts"],
      use: { ...devices["Desktop Chrome"], baseURL: BASE_URL },
    },
  ],
  webServer: [
    {
      // Real create_app over seeded SQLite. cwd=repo root so `tests.e2e_backend` imports; PYTHONPATH
      // points at `src/` so `gw_geo` imports (the package isn't pip-installed — pytest uses the same
      // pythonpath). The DB is (re)seeded from scratch on every boot.
      command: `${PYTHON} -m uvicorn tests.e2e_backend:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: REPO_ROOT,
      url: `${API_URL}/healthz`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: { PYTHONPATH: path.join(REPO_ROOT, "src") },
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      // Proxied Next server (real project): API_PROXY_TARGET turns on the same-origin rewrite;
      // NEXT_PUBLIC_API_URL stays empty so the browser calls the API same-origin (via the rewrite).
      command: `npm run dev -- --port ${PORT}`,
      url: `${BASE_URL}/login`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      // NEXT_DIST_DIR isolates this server's build cache from the plain one below (two dev servers,
      // one directory).
      env: {
        API_PROXY_TARGET: API_URL,
        NEXT_PUBLIC_API_URL: "",
        NEXT_DIST_DIR: ".next-e2e-real",
      },
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      // Plain Next server (mocked project): no proxy — unmocked requests 404 on Next, exactly as in
      // the app's normal dev mode, so the pre-existing mocked specs behave unchanged.
      command: `npm run dev -- --port ${MOCK_PORT}`,
      url: `${MOCK_URL}/login`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: {
        API_PROXY_TARGET: "",
        NEXT_PUBLIC_API_URL: "",
        NEXT_DIST_DIR: ".next-e2e-mock",
      },
      stdout: "pipe",
      stderr: "pipe",
    },
  ],
});
