/**
 * EVERY PAGE AT FOUR WIDTHS (campaign session 4, §A.2): 1440, 1024, 768 and 375 px.
 *
 * Every page of the route catalogue (support/routes.ts) is opened as the Enterprise owner (the
 * platform pages as platform staff) at each width, and must:
 *   - never scroll sideways: the page is as wide as the window (a table may scroll inside its own
 *     box, the page may not);
 *   - show no raw internal value: no SCREAMING_SNAKE_CASE key (PENDING_REVIEW, OWN_KEY_NEEDED, ...)
 *     in the text a person reads, outside code samples and inputs.
 * A screenshot of each page and width is attached to the test, so the layout can be looked at.
 */
import type { Page, TestInfo } from "@playwright/test";

import { test, expect, settle } from "../support/fixtures";
import { org, ws } from "../support/env";
import { ORGANIZATION_PAGES, PLATFORM_PAGES, WORKSPACE_PAGES } from "../support/routes";

const WIDTHS = [1440, 1024, 768, 375] as const;
// Shown on purpose, and only on a developer machine: the billing page names the missing test key
// ("STRIPE_SECRET_KEY is not set ... add a test-mode key to backend/.env"). In production the same
// spot says "Paid checkout is temporarily unavailable" (portal_service.gateway_readiness).
const DEVELOPER_ONLY = new Set(["STRIPE_SECRET_KEY", "DODO_API_KEY"]);
const HEIGHT = { 1440: 900, 1024: 768, 768: 1024, 375: 812 } as const;

async function check(page: Page, testInfo: TestInfo, where: string, width: number): Promise<void> {
  await settle(page);
  const found = await page.evaluate(() => {
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
  found.raw = found.raw.filter((value) => !DEVELOPER_ONLY.has(value));
  await testInfo.attach(`${where} @ ${width}px`, {
    body: await page.screenshot({ fullPage: false }),
    contentType: "image/png",
  });
  expect(found.overflow, `${where} at ${width}px scrolls sideways by ${found.overflow}px`).toBeLessThanOrEqual(1);
  expect(found.raw, `${where} at ${width}px shows raw internal values`).toEqual([]);
}

for (const width of WIDTHS) {
  test.describe(`layout at ${width}px — every app page`, () => {
    test.use({ user: "C.owner", viewport: { width, height: HEIGHT[width] } });

    for (const target of WORKSPACE_PAGES) {
      test(`workspace ${target.id}`, async ({ page }, testInfo) => {
        await page.goto(ws("C", target.sub));
        await expect(page.locator("body")).toContainText(target.title);
        await check(page, testInfo, target.id, width);
      });
    }
    for (const target of ORGANIZATION_PAGES) {
      test(`organization ${target.id}`, async ({ page }, testInfo) => {
        await page.goto(org("C", target.sub));
        await expect(page.locator("body")).toContainText(target.title);
        await check(page, testInfo, target.id, width);
      });
    }
  });

  test.describe(`layout at ${width}px — platform pages`, () => {
    test.use({ user: "P.superadmin", viewport: { width, height: HEIGHT[width] } });
    for (const target of PLATFORM_PAGES) {
      test(`platform ${target.id}`, async ({ page }, testInfo) => {
        await page.goto(target.path);
        await expect(page.locator("body")).toContainText(target.title);
        await check(page, testInfo, target.id, width);
      });
    }
  });

  test.describe(`layout at ${width}px — signed out`, () => {
    test.use({ viewport: { width, height: HEIGHT[width] } });
    for (const path of ["/login", "/register", "/forgot-password", "/pricing", "/terms", "/privacy"]) {
      test(`public ${path}`, async ({ page }, testInfo) => {
        await page.goto(path);
        await check(page, testInfo, path, width);
      });
    }
  });
}
