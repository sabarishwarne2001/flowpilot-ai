/**
 * ACCESSIBILITY (campaign session 4, §A.3): axe-core finds no serious or critical WCAG 2.1 A or AA
 * violation on any page, in the light and the dark theme.
 *
 * Every page of the route catalogue (support/routes.ts) is opened as the Enterprise owner, which
 * unlocks them all, then the public pages signed out. A violation names the page, the rule and the
 * element, so it can be fixed where it is. Moderate and minor findings are recorded on the test as
 * annotations, not failures.
 */
import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";

import { test, expect, settle } from "../support/fixtures";
import { org, ws } from "../support/env";
import { ORGANIZATION_PAGES, PLATFORM_PAGES, WORKSPACE_PAGES } from "../support/routes";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

async function audit(page: Page, testInfo: TestInfo, where: string): Promise<void> {
  await settle(page);
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  for (const v of results.violations.filter((v) => !blocking.includes(v))) {
    testInfo.annotations.push({ type: `axe ${v.impact}`, description: `${where}: ${v.id} (${v.nodes.length})` });
  }
  expect(
    blocking.map((v) => `${v.impact} ${v.id}: ${v.help} — ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`),
    `${where} has serious or critical accessibility violations`,
  ).toEqual([]);
}

for (const scheme of ["light", "dark"] as const) {
  test.describe(`accessibility, ${scheme} theme — every app page`, () => {
    test.use({ user: "C.owner", colorScheme: scheme });

    for (const target of WORKSPACE_PAGES) {
      test(`workspace ${target.id}`, async ({ page }, testInfo) => {
        await page.goto(ws("C", target.sub));
        await expect(page.locator("body")).toContainText(target.title);
        await audit(page, testInfo, target.id);
      });
    }
    for (const target of ORGANIZATION_PAGES) {
      test(`organization ${target.id}`, async ({ page }, testInfo) => {
        await page.goto(org("C", target.sub));
        await expect(page.locator("body")).toContainText(target.title);
        await audit(page, testInfo, target.id);
      });
    }
  });

  test.describe(`accessibility, ${scheme} theme — platform pages`, () => {
    test.use({ user: "P.superadmin", colorScheme: scheme });
    for (const target of PLATFORM_PAGES) {
      test(`platform ${target.id}`, async ({ page }, testInfo) => {
        await page.goto(target.path);
        await expect(page.locator("body")).toContainText(target.title);
        await audit(page, testInfo, target.id);
      });
    }
  });

  test.describe(`accessibility, ${scheme} theme — a Developer workspace`, () => {
    // Its sidebar shows the Business and Enterprise pages as locked items, which the Enterprise
    // owner above never sees.
    test.use({ user: "A.owner", colorScheme: scheme });
    test("locked items in the sidebar", async ({ page }, testInfo) => {
      await page.goto(ws("A", ""));
      await expect(page.getByRole("button", { name: /\(not included in your plan\)$/ }).first()).toBeVisible();
      await audit(page, testInfo, "developer dashboard");
    });
  });

  test.describe(`accessibility, ${scheme} theme — signed out`, () => {
    test.use({ colorScheme: scheme });
    // Website session (B7): /pricing, /terms and /privacy are the website's pages now (spec 70
    // checks the redirect); the website CI job checks their accessibility in both themes.
    for (const path of ["/login", "/register", "/forgot-password"]) {
      test(`public ${path}`, async ({ page }, testInfo) => {
        await page.goto(path);
        await audit(page, testInfo, path);
      });
    }
  });
}
