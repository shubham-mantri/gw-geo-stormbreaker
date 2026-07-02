import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright e2e config. Boots `next dev` via `webServer` so specs can drive the
 * real app. The login spec mocks `**\/auth/login` at the network layer.
 *
 * Uses a dedicated port (override with WEB_E2E_PORT) to avoid colliding with
 * other dev servers on the default :3000.
 */
const PORT = Number(process.env.WEB_E2E_PORT ?? 4488);
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "line" : "list",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run dev -- --port ${PORT}`,
    url: `${BASE_URL}/login`,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
    stdout: "pipe",
    stderr: "pipe",
  },
});
