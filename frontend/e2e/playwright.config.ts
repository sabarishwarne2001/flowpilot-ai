import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/**
 * FlowPilot AI browser tests (Phase 3). See e2e/README.md for how to run them.
 *
 * The backend (API, worker, Postgres, Redis) is started outside Playwright by
 * e2e/scripts/start-stack.sh; global-setup.ts checks that it answers, runs the
 * idempotent seed and uploads the sample documents. Playwright itself only
 * starts the web app: by default the PRODUCTION bundle under `vite preview`,
 * with /api proxied to the local API (same origin, as behind Caddy). Set
 * E2E_MODE=dev to test the Vite dev server instead.
 */
const MODE = process.env.E2E_MODE === "dev" ? "dev" : "preview";
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3000);
const API_ORIGIN = process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8000";

/**
 * N-015. E2E_CSP=1 serves the bundle with the production Content-Security-Policy enforced. The
 * policy is read from the Caddyfile, the one place production takes it from, so the suite tests
 * exactly what customers get.
 */
function productionCsp(): string | undefined {
  if (process.env.E2E_CSP !== "1") return undefined;
  const caddyfile = fs.readFileSync(path.resolve(HERE, "..", "..", "backend", "deploy", "Caddyfile"), "utf8");
  const match = /Content-Security-Policy(?:-Report-Only)?\s+"([^"]+)"/.exec(caddyfile);
  if (!match) throw new Error("E2E_CSP=1 but no Content-Security-Policy header was found in backend/deploy/Caddyfile");
  return match[1];
}
const CSP = productionCsp();

const webCommand =
  MODE === "dev"
    ? `npx vite --port ${WEB_PORT} --strictPort`
    : `npx vite build --logLevel error && npx vite preview --port ${WEB_PORT} --strictPort`;

export default defineConfig({
  testDir: process.env.E2E_TEST_DIR ?? "./tests",
  outputDir: "./test-results",
  globalSetup: "./global-setup.ts",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: Number(process.env.E2E_WORKERS ?? 2),
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: [
    ["list"],
    ["json", { outputFile: "./test-results/results.json" }],
    ["html", { outputFolder: "./playwright-report", open: "never" }],
  ],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    headless: true,
    viewport: { width: 1440, height: 900 },
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off",
    launchOptions: process.env.E2E_CHROMIUM_PATH
      ? { executablePath: process.env.E2E_CHROMIUM_PATH }
      : {},
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: webCommand,
    url: `http://localhost:${WEB_PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 240_000,
    cwd: "..",
    env: {
      BROWSER: "none",
      E2E_API_PROXY: API_ORIGIN,
      ...(CSP ? { E2E_CSP_HEADER: CSP } : {}),
      ...(MODE === "dev" ? { VITE_API_URL: `${API_ORIGIN}/api/v1` } : {}),
    },
    stdout: "ignore",
    stderr: "pipe",
  },
});
