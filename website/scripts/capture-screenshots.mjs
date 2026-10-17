// Real screenshots of the product for the website, in light and dark.
//
//   APP_URL=http://localhost:3000 CAPTURE_EMAIL=... CAPTURE_PASSWORD=... npm run screenshots [-- name ...]
//
// It signs in to a RUNNING Servabo with demonstration data and photographs each page the website
// shows, at 1440 x 900, once with the light theme and once with the dark one, into
// src/assets/screenshots/<name>-<theme>.png. The website build refuses to start when one is
// missing, so the site never shows a mock-up.
//
// Demonstration data: start the stack the browser tests use (`E2E_LLM=1 frontend/e2e/scripts/
// start-stack.sh`), run `python scripts/seed_website_demo.py` in backend/ (an Enterprise
// organization, Meridian Wholesale Pvt Ltd, with one owner), and serve the production bundle
// (`E2E_API_PROXY=http://127.0.0.1:8000 npx vite preview --port 3000` in frontend/). The script
// builds Helm's sample company there (an invented Pune distributor, nine months of documents,
// labelled "sample" everywhere), puts some work through the engines, and photographs it.
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const APP = (process.env.APP_URL ?? "http://localhost:3000").replace(/\/$/, "");
// backend/scripts/seed_website_demo.py creates this owner and its Enterprise organization.
const EMAIL = process.env.CAPTURE_EMAIL ?? "asha.rao@demo.example.com";
const PASSWORD = process.env.CAPTURE_PASSWORD ?? "E2e-Servabo-Pass-2026!";
const ORG = process.env.CAPTURE_ORG ?? "meridian-wholesale";
// The sample company's own workspace: nine months of realistic documents from invented suppliers.
const WS = process.env.CAPTURE_WORKSPACE ?? "sample-meridian";
const OUT = fileURLToPath(new URL("../src/assets/screenshots/", import.meta.url));
const VIEWPORT = { width: 1440, height: 900 };

const ws = (sub = "") => `/${ORG}/${WS}${sub ? `/${sub}` : ""}`;
const org = (sub = "") => `/organizations/${ORG}${sub ? `/${sub}` : ""}`;
const helm = (sub = "") => `/${ORG}/helm${sub ? `/${sub}` : ""}`;

async function settle(page) {
  await page.waitForLoadState("networkidle", { timeout: 30_000 }).catch(() => {});
  await page.waitForFunction(() => !document.querySelector('[aria-busy="true"], .animate-pulse'), null, { timeout: 15_000 }).catch(() => {});
  await page.waitForTimeout(600);
}

async function openFirst(page, locator) {
  const first = locator.first();
  // Lists load after the page; give the first entry a moment to appear.
  await first.waitFor({ state: "attached", timeout: 15_000 }).catch(() => {});
  if (!(await first.count())) return false;
  // Follow a link's address (a click can land on whatever a hover moved underneath it).
  const href = await first.getAttribute("href");
  if (href) await page.goto(new URL(href, page.url()).toString());
  else await first.click();
  await settle(page);
  return true;
}

// An invoice's detail (the table rows open on click; the narrow-screen list has the links).
const invoice = (page) => page.locator("main a[href*='/work-items/']", { hasText: /^[A-Z]{2,4}-\d{4}\.pdf$/ });

/** Each shot: where it is, and what to do there before the picture. */
const SHOTS = {
  extraction: {
    path: ws("work-items"),
    prepare: (page) => openFirst(page, invoice(page)),
  },
  memory: { path: ws("extraction-memory") },
  matching: { path: ws("procurement"), prepare: (page) => openFirst(page, page.locator("main a[href*='/procurement/']:not([href$='/policies'])")) },
  radar: { path: ws("radar") },
  truthmesh: { path: ws("truthmesh") },
  obligations: { path: ws("obligations") },
  process: { path: ws("process") },
  audit: { path: org("audit") },
  review: { path: ws("verification") },
  redaction: {
    path: ws("work-items"),
    prepare: async (page) => {
      if (await openFirst(page, invoice(page))) {
        const redact = page.getByRole("button", { name: /Redact/ }).first();
        if (await redact.count()) {
          await redact.click();
          await settle(page);
        }
      }
    },
  },
  identity: { path: org("identity") },
  egress: { path: org("egress") },
  "helm-cockpit": { path: helm(), helm: true },
  // The payment run, with the payments approved by demoActivity() on their run days.
  "helm-plan": { path: helm("plan"), helm: true },
  "helm-certificate": {
    path: helm("plan"),
    helm: true,
    // One payment's Guard certificate, opened from the plan.
    prepare: async (page) => {
      const certificate = page.getByRole("main").getByRole("button", { name: /^Certificate/ }).or(page.getByRole("main").getByRole("link", { name: /^Certificate/ })).first();
      if (await certificate.count()) {
        await certificate.click();
        await settle(page);
      }
    },
  },
  "helm-time-machine": {
    path: helm("time-machine"),
    helm: true,
    prepare: async (page) => {
      const report = page.getByTestId("helm-backtest-report");
      if (!(await report.count())) {
        const months = page.getByRole("combobox", { name: "Replay the last" });
        if (await months.count()) await months.selectOption("9").catch(() => {});
        const replay = page.getByRole("button", { name: "Replay with my mandates" });
        if (await replay.count()) await replay.click();
        await report.waitFor({ timeout: 180_000 }).catch(() => {});
      }
      await settle(page);
    },
  },
  "helm-bank": { path: helm("bank"), helm: true, prepare: (page) => importBankStatement(page) },
  "helm-reconciliation": {
    path: helm(),
    helm: true,
    prepare: async (page) => {
      const action = page.getByTestId("helm-action").filter({ hasText: /Reconcile/ }).first();
      if (await action.count()) {
        await action.scrollIntoViewIfNeeded();
        await action.getByRole("button", { name: "Why, and what the Guard checked" }).click().catch(() => {});
        await settle(page);
      }
    },
  },
};

/** The API, as the signed-in owner (the preview server forwards /api to it). */
async function apiSession() {
  const login = await fetch(`${APP}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username: EMAIL, password: PASSWORD }),
  });
  if (!login.ok) throw new Error(`API sign-in failed: HTTP ${login.status}`);
  const { access_token: token } = await login.json();
  return async (method, path, body) => {
    const response = await fetch(`${APP}/api/v1${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`${method} ${path}: HTTP ${response.status} ${(await response.text()).slice(0, 200)}`);
    return response.json();
  };
}

/** What a person would have done by now: approve this week's payments the Guard passed. */
let demo = null;
async function demoActivity() {
  if (demo) return demo;
  const api = await apiSession();
  const organization = (await api("GET", "/me/organizations")).find((o) => o.slug === ORG);
  const base = `/organizations/${organization.id}/helm`;
  const book = (await api("GET", base)).books.find((b) => b.sample);
  const cockpit = await api("GET", `${base}/books/${book.id}/cockpit`);
  const payments = cockpit.actions.filter((a) => a.kind === "schedule_payment" && a.guard_passed);
  const run = await api("GET", `${base}/books/${book.id}/payment-run`);
  if (!run.days.length) {
    for (const action of payments.slice(0, 4)) await api("POST", `${base}/actions/${action.id}/approve`);
  }
  demo = { payments };
  return demo;
}

/** Helm's bank page after an import: one line Helm matched, one it suggests, one bank fee. */
async function importBankStatement(page) {
  if (await page.getByTestId("helm-bank-account").count()) return;
  const [first, second] = (await demoActivity()).payments;
  const rupees = (micros) => (micros / 1_000_000).toFixed(2);
  await page.getByRole("textbox", { name: "Account name" }).fill("HDFC current account");
  await page.getByRole("textbox", { name: "Account number" }).fill("50200099887766");
  await page.getByRole("textbox", { name: "Currency" }).fill("INR");
  await page.getByRole("button", { name: "Add account" }).click();
  await page.getByTestId("helm-bank-account").waitFor();
  let balance = 4_250_000;
  const line = (date, text, ref, amount) => `${date},${text},${ref},${amount},,${(balance -= Number(amount)).toFixed(2)}`;
  const csv = [
    "HDFC Bank current account statement",
    "Date,Description,Reference,Debit,Credit,Balance",
    line(first.run_date, `NEFT ${first.title.replace(/^Pay (.+?) ₹.*$/, "$1").toUpperCase()} ${first.params.document_number}`, "N4471", rupees(first.amount_micros)),
    line(second.run_date, "NEFT OUTWARD 99812", "N4472", rupees(second.amount_micros)),
    line(second.run_date, "ACCOUNT MAINTENANCE CHARGES", "C0910", "590.00"),
  ].join("\n");
  await page.getByLabel("Statement file").setInputFiles({ name: "hdfc-oct.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByTestId("helm-bank-preview").getByRole("button", { name: "Import this statement" }).click();
  await page.getByTestId("helm-bank-result").waitFor({ timeout: 60_000 });
  // Let the "account added" notice fade before the picture.
  await page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 15_000 }).catch(() => {});
  await settle(page);
}

async function signIn(page) {
  await page.goto(`${APP}/login`);
  // Two steps: the email first (single sign-on discovery), then the password.
  await page.getByRole("textbox", { name: "Email" }).fill(EMAIL);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.locator("#password").fill(PASSWORD);
  await page.locator("#password").press("Enter");
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), { timeout: 30_000 });
  await settle(page);
}

/** Helm's pages show the sample company: build it once, then select its book. */
async function ensureSampleCompany(page) {
  await page.goto(`${APP}${helm("start")}`);
  await settle(page);
  const sample = page.getByTestId("helm-sample");
  const open = sample.getByRole("button", { name: /^Open Meridian Wholesale/ });
  if (!(await open.count())) {
    const build = sample.getByRole("button", { name: "Explore Helm with a sample company" });
    if (await build.count()) await build.click();
    await open.waitFor({ timeout: 300_000 });
  }
  await open.click();
  await settle(page);
}

const only = process.argv.slice(2);
const names = only.length ? only : Object.keys(SHOTS);
await mkdir(OUT, { recursive: true });
const browser = await chromium.launch();
const failures = [];
try {
  for (const theme of ["light", "dark"]) {
    // An Indian reader of the Pune sample company: rupees in lakh grouping, dates on Indian time.
    const context = await browser.newContext({
      viewport: VIEWPORT,
      colorScheme: theme,
      deviceScaleFactor: 1,
      locale: "en-IN",
      timezoneId: "Asia/Kolkata",
    });
    // The app follows the system theme unless a person chose one; make sure it is the system.
    await context.addInitScript(() => {
      try {
        const key = "servabo_ui_preferences";
        const saved = JSON.parse(window.localStorage.getItem(key) || "{}");
        saved.state = { ...(saved.state || {}), theme: "system" };
        window.localStorage.setItem(key, JSON.stringify(saved));
      } catch {
        /* nothing saved yet */
      }
    });
    const page = await context.newPage();
    await signIn(page);
    // Every page shows the sample company, so it is built (or found) first.
    await ensureSampleCompany(page);
    await demoActivity();
    for (const name of names) {
      const shot = SHOTS[name];
      if (!shot) {
        failures.push(`unknown shot ${name}`);
        continue;
      }
      try {
        await page.goto(`${APP}${shot.path}`);
        await settle(page);
        if (shot.prepare) await shot.prepare(page);
        await page.screenshot({ path: `${OUT}${name}-${theme}.png` });
        console.log(`${name}-${theme}.png`);
      } catch (error) {
        failures.push(`${name} (${theme}): ${error}`);
      }
    }
    await context.close();
  }
} finally {
  await browser.close();
}
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
