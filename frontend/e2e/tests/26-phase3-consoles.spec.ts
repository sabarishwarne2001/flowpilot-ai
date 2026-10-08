/**
 * PHASE 3 — organization consoles, platform administration, settings and the shell, driven the
 * way a person uses them. Each test named after a finding (F-1xx) failed on the code before its
 * fix (docs/hardening/FINDINGS.md, "Phase 3").
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, resolveWorkspaceId } from "../support/api";
import { TENANTS, org } from "../support/env";

interface NoticePage {
  readonly items: readonly { id: string; title: string; is_read: boolean }[];
  readonly total: number;
}

test.describe("Organization notifications (F-184)", () => {
  test.use({ user: "B.owner" });

  test("reading the last unread notice on page 2 goes back to a page that has notices", async ({ page, session }) => {
    const { organizationId: orgId } = await resolveWorkspaceId(session!, TENANTS.B.org, TENANTS.B.ws);
    // The seed gives this owner 30 notices (scripts/seed_e2e.py). 26 unread: page 2 of the
    // unread filter then holds exactly one.
    const listed = await api<NoticePage>(session, "GET", `/organizations/${orgId}/notifications?limit=100`);
    const seeded = listed.body.items.filter((item) => item.title.startsWith("E2E notice")).sort((a, b) => a.title.localeCompare(b.title));
    expect(seeded.length).toBeGreaterThanOrEqual(30);
    const others = listed.body.items.filter((item) => !item.title.startsWith("E2E notice"));
    for (const [index, item] of [...seeded, ...others].entries()) {
      const unread = index < 26;
      if (item.is_read === !unread) continue;
      const patched = await api(session, "PATCH", `/organizations/${orgId}/notifications/${item.id}`, { is_read: !unread });
      expect(patched.status).toBe(200);
    }

    await page.goto(org("B", "notifications"));
    await page.getByRole("checkbox", { name: "Unread only" }).check();
    await expect(page.getByText(/1–25 of 26/)).toBeVisible();
    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByText(/26–26 of 26/)).toBeVisible();
    const notices = page.getByRole("listitem").filter({ hasText: "E2E notice" });
    await expect(notices).toHaveCount(1);
    await notices.getByRole("button", { name: /Mark as read/ }).click();

    // Before the fix: "Nothing matches these filters", "25 total" and no way back to page 1.
    await expect(page.getByText("Nothing matches these filters")).toHaveCount(0);
    await expect(notices).toHaveCount(25);
    await expectHealthyPage(page);
  });

  test("changing a filter keeps the filters on screen while the list loads", async ({ page }) => {
    await page.goto(org("B", "notifications"));
    await settle(page);
    // A slow network, so the moment between the request and the answer can be seen.
    await page.route(/\/organizations\/[^/]+\/notifications\?/, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1500));
      await route.continue();
    });
    const category = page.getByRole("combobox", { name: "Category" });
    await category.selectOption("SECURITY");
    // Before the fix the whole page was swapped for "Loading notifications…": the controls
    // disappeared under the pointer and came back a moment later.
    await expect(page.getByText("Loading notifications…")).toHaveCount(0, { timeout: 1000 });
    await expect(category).toBeVisible({ timeout: 1000 });
    await expect(page.getByRole("checkbox", { name: "Unread only" })).toBeVisible({ timeout: 1000 });
    await settle(page);
    await expectHealthyPage(page);
  });
});

test.describe("Organization console breadcrumb (F-185)", () => {
  test.use({ user: "C.owner" });

  test("the bar above each console page names that page, and the page has one main heading", async ({ page }) => {
    for (const [sub, label] of [
      ["notifications", "Notifications"],
      ["members", "Members"],
      ["billing", "Billing"],
      ["webhooks", "Webhooks"],
    ] as const) {
      await page.goto(org("C", sub));
      const crumbs = page.getByRole("navigation", { name: "Breadcrumb" });
      // Before the fix every page said "Caretakers Global Inc › Settings", in a second <h1>.
      await expect(crumbs).toContainText(TENANTS.C.name);
      await expect(crumbs.locator("[aria-current=page]")).toHaveText(label);
      await expect(page.locator("h1")).toHaveCount(1);
    }
    await expectHealthyPage(page);
  });
});
