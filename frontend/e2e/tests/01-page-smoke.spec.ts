/**
 * PAGE SMOKE: every page renders for a role that is allowed to see it, on the
 * plan that unlocks it (Tenant C, Enterprise), with zero page errors, zero
 * console errors and zero failed API calls. Counts of visible buttons, links
 * and inputs are attached to each test as annotations (copied to the ledger).
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { org, ws } from "../support/env";
import { ORGANIZATION_PAGES, PLATFORM_PAGES, WORKSPACE_PAGES } from "../support/routes";
import type { Page, TestInfo } from "@playwright/test";

async function recordCounts(page: Page, testInfo: TestInfo): Promise<void> {
  const counts = await page.evaluate(() => {
    const main = document.querySelector("main") ?? document.body;
    const visible = (el: Element) => {
      const rect = (el as HTMLElement).getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    return {
      buttons: [...main.querySelectorAll("button,[role=button]")].filter(visible).length,
      disabled: [...main.querySelectorAll("button:disabled")].filter(visible).length,
      links: [...main.querySelectorAll("a[href]")].filter(visible).length,
      inputs: [...main.querySelectorAll("input,select,textarea")].filter(visible).length,
    };
  });
  testInfo.annotations.push({
    type: "counts",
    description: `buttons=${counts.buttons} (disabled=${counts.disabled}) links=${counts.links} inputs=${counts.inputs}`,
  });
}

test.describe("page smoke — Enterprise owner (Tenant C)", () => {
  test.use({ user: "C.owner" });

  for (const target of WORKSPACE_PAGES) {
    test(`workspace page ${target.id}`, async ({ page }, testInfo) => {
      await page.goto(ws("C", target.sub));
      await expect(page.locator("body")).toContainText(target.title);
      await settle(page);
      await expectHealthyPage(page);
      await recordCounts(page, testInfo);
    });
  }

  for (const target of ORGANIZATION_PAGES) {
    test(`organization page ${target.id}`, async ({ page }, testInfo) => {
      await page.goto(org("C", target.sub));
      await expect(page.locator("body")).toContainText(target.title);
      await settle(page);
      await expectHealthyPage(page);
      await recordCounts(page, testInfo);
    });
  }

  test("the organization root redirects to General", async ({ page }) => {
    await page.goto(org("C"));
    await expect(page).toHaveURL(/\/organizations\/caretakers-global\/settings$/);
    await expect(page.locator("body")).toContainText(/Organization profile/);
  });

  test("an unknown page shows the 404 page, not a crash", async ({ page }) => {
    await page.goto("/this/page/does-not-exist/at-all");
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  });

  test("legacy paths redirect into the workspace", async ({ page }) => {
    for (const legacy of ["/", "/work-items", "/assistant", "/automation", "/notifications", "/settings"]) {
      await page.goto(legacy);
      await expect(page).toHaveURL(/\/caretakers-global\/(operations|finance)/);
      await expectHealthyPage(page);
    }
  });

  test("the workspace picker lists both Enterprise workspaces", async ({ page }) => {
    await page.goto("/workspaces");
    await expect(page.locator("body")).toContainText("Caretakers Global Inc");
    await expect(page.locator("body")).toContainText("Operations");
    await expect(page.locator("body")).toContainText("Finance");
  });
});

test.describe("page smoke — platform super-admin", () => {
  test.use({ user: "P.superadmin" });

  for (const target of PLATFORM_PAGES) {
    test(`platform page ${target.id}`, async ({ page }, testInfo) => {
      await page.goto(target.path);
      await expect(page.locator("body")).toContainText(target.title);
      await settle(page);
      await expectHealthyPage(page);
      await recordCounts(page, testInfo);
    });
  }
});

test.describe("page smoke — public pages (signed out)", () => {
  const PUBLIC = [
    { path: "/login", text: /sign in|log in|welcome back/i },
    { path: "/register", text: /create|sign up|register/i },
    { path: "/forgot-password", text: /forgot|reset/i },
    { path: "/reset-password", text: /reset|link|invalid|expired/i },
    { path: "/verify-email", text: /verif|link|invalid|expired/i },
    { path: "/invitations/accept", text: /invitation|invite|link|invalid/i },
  ];
  for (const target of PUBLIC) {
    test(`public page ${target.path}`, async ({ page }) => {
      await page.goto(target.path);
      await expect(page.locator("body")).toContainText(target.text);
      await settle(page);
      await expectHealthyPage(page);
    });
  }

  test("a signed-out visitor is sent to /login from a workspace page", async ({ page }) => {
    await page.goto(ws("C", "work-items"));
    await expect(page).toHaveURL(/\/login/);
  });
});
