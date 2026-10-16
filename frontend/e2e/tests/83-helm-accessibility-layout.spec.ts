/**
 * HELM PAGES: accessibility and layout (flagship session, box 8), the checks of specs 72 and 74 on
 * every Helm page.
 *
 * An Enterprise customer builds the sample company, so every Helm page has something on it (the
 * instruments, the plan, the ledger, a replay, bank statements, certificates). Each page must have no
 * serious or critical WCAG 2.1 A/AA violation (axe-core) in the light and the dark theme, and at
 * 1440, 1024, 768 and 375 px it must not scroll sideways or show a raw internal value
 * (SCREAMING_SNAKE_CASE) in the text a person reads.
 */
import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";

import { test, expect, settle } from "../support/fixtures";
import { api } from "../support/api";
import { newCustomer, signInAs, standIn, type Customer } from "../support/journey";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const WIDTHS = [1440, 1024, 768, 375] as const;
const HEIGHT = { 1440: 900, 1024: 768, 768: 1024, 375: 812 } as const;
const PAGES: readonly { sub: string; heading: RegExp }[] = [
  { sub: "", heading: /^Cockpit$/ },
  { sub: "plan", heading: /^Plan$/ },
  { sub: "mandates", heading: /^Mandates$/ },
  { sub: "ledger", heading: /^Promise ledger$/ },
  { sub: "time-machine", heading: /^Time machine$/ },
  { sub: "bank", heading: /^Bank statements$/ },
  { sub: "certificates", heading: /^Certificates$/ },
  { sub: "settings", heading: /^Settings$/ },
  { sub: "start", heading: /Helm/ },
];

let customer: Customer;
let bookId: string;

const open = async (page: Page, sub: string, heading: RegExp): Promise<void> => {
  await page.goto(`/${customer.orgSlug}/helm${sub ? `/${sub}` : ""}?book=${bookId}`);
  await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  await settle(page);
};

async function audit(page: Page, testInfo: TestInfo, where: string): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  for (const v of results.violations.filter((v) => !blocking.includes(v))) {
    testInfo.annotations.push({ type: `axe ${v.impact}`, description: `${where}: ${v.id} (${v.nodes.length})` });
  }
  return blocking.map((v) => `${where} — ${v.impact} ${v.id}: ${v.help} — ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
}

async function layout(page: Page): Promise<{ overflow: number; raw: string[] }> {
  return page.evaluate(() => {
    const overflow = document.documentElement.scrollWidth - window.innerWidth;
    const main = document.querySelector("main") ?? document.body;
    const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
    const raw: string[] = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement;
      if (!parent || parent.closest("code, pre, kbd, samp, textarea, input, select, option, [data-raw-value]")) continue;
      const style = window.getComputedStyle(parent);
      if (style.display === "none" || style.visibility === "hidden" || parent.closest("[aria-hidden='true'], .sr-only")) continue;
      for (const match of (node.textContent ?? "").match(/\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g) ?? []) raw.push(match);
    }
    return { overflow, raw: [...new Set(raw)].slice(0, 8) };
  });
}

test.describe("Helm pages — accessibility and layout", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(420_000);

  test.beforeAll(async () => {
    test.setTimeout(420_000);
    customer = await newCustomer("helmpages", "Helm Pages Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "enterprise", "--seats", "3");
    const base = `/organizations/${customer.orgId}/helm`;
    const built = await api(customer.session, "POST", `${base}/sample`);
    expect(built.status, built.text.slice(0, 300)).toBe(202);
    await expect
      .poll(async () => {
        const overview = await api<{ sample: { book_id: string | null; building: boolean } }>(customer.session, "GET", base);
        bookId = overview.body.sample?.book_id ?? "";
        return Boolean(bookId) && !overview.body.sample.building;
      }, { timeout: 300_000, intervals: [3_000, 5_000] })
      .toBe(true);
  });

  for (const scheme of ["light", "dark"] as const) {
    test(`no serious or critical violation, ${scheme} theme`, async ({ page }, testInfo) => {
      await page.emulateMedia({ colorScheme: scheme });
      await signInAs(page, customer.email);
      const found: string[] = [];
      for (const target of PAGES) {
        await open(page, target.sub, target.heading);
        found.push(...(await audit(page, testInfo, `/helm/${target.sub}`)));
      }
      // The tour's dialog is a page state of its own.
      await page.goto(`/${customer.orgSlug}/helm?book=${bookId}&tour=2`);
      await expect(page.getByTestId("helm-tour")).toBeVisible();
      await settle(page);
      found.push(...(await audit(page, testInfo, "/helm?tour=2")));
      expect(found, "Helm pages with serious or critical accessibility violations").toEqual([]);
    });
  }

  for (const width of WIDTHS) {
    test(`no sideways scroll and no raw values at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: HEIGHT[width] });
      await signInAs(page, customer.email);
      const problems: string[] = [];
      for (const target of PAGES) {
        await open(page, target.sub, target.heading);
        const found = await layout(page);
        await testInfo.attach(`/helm/${target.sub} @ ${width}px`, { body: await page.screenshot(), contentType: "image/png" });
        if (found.overflow > 1) problems.push(`/helm/${target.sub} scrolls sideways by ${found.overflow}px`);
        if (found.raw.length) problems.push(`/helm/${target.sub} shows raw values ${found.raw.join(", ")}`);
      }
      expect(problems, `Helm pages at ${width}px`).toEqual([]);
    });
  }
});
