/**
 * PHASE 3 — organization consoles, platform administration, settings and the shell, driven the
 * way a person uses them. Each test named after a finding (F-1xx) failed on the code before its
 * fix (docs/hardening/FINDINGS.md, "Phase 3").
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, resolveWorkspaceId } from "../support/api";
import { STATE_FILE, TENANTS, org, runId } from "../support/env";

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

test.describe("Revenue operations: invoiced contracts (F-186)", () => {
  test.use({ user: "P.superadmin" });

  test("mark an invoice paid and cancel the contract through dialogs, never a browser prompt", async ({ page, session }) => {
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8")) as { seed: { tenants: Record<string, { organization_id: string }> } };
    const orgId = state.seed.tenants.P!.organization_id;
    // One active contract per organization: end any a previous run left active.
    const contracts = await api<{ id: string; organization_id: string; status: string }[]>(session, "GET", "/admin/revops/contracts");
    for (const row of contracts.body.filter((c) => c.organization_id === orgId && c.status === "ACTIVE")) {
      expect((await api(session, "POST", `/admin/revops/contracts/${row.id}/end`, { reason: "CANCELLED" })).status).toBe(200);
    }
    let prompts = 0;
    page.on("dialog", (dialog) => {
      prompts += 1;
      void dialog.dismiss();
    });

    await page.goto("/admin/revops");
    await page.getByRole("tab", { name: "Contracts" }).or(page.getByRole("button", { name: "Contracts", exact: true })).click();
    const number = `ENT-E2E-${runId()}`;
    const form = page.locator("form", { has: page.getByRole("heading", { name: "Draft a contract" }) });
    const organization = form.getByLabel(/^Organization/);
    if ((await organization.evaluate((el) => el.tagName)) === "SELECT") {
      await organization.selectOption(orgId);
    } else {
      await organization.fill(orgId);
    }
    await form.getByLabel("Contract number").fill(number);
    await form.getByLabel("Plan").selectOption("business");
    await form.getByLabel("Amount per period").fill("1200");
    const end = new Date(Date.now() + 365 * 86_400_000).toISOString().slice(0, 10);
    await form.getByLabel("Term end").fill(end);
    await form.getByRole("button", { name: "Draft contract" }).click();

    const detail = page.getByTestId("contract-detail");
    await expect(detail).toContainText(new RegExp(number, "i"));
    await detail.getByRole("button", { name: "Activate" }).click();
    await expect(detail).toContainText("ACTIVE");
    const invoice = detail.getByRole("row").filter({ has: page.getByRole("button", { name: "Mark paid" }) }).first();
    await expect(invoice).toBeVisible();

    // Before the fix "Mark paid" opened window.prompt; dismissing it still sent the request with an
    // empty reference (422, "The action failed.").
    await invoice.getByRole("button", { name: "Mark paid" }).click();
    const pay = page.getByRole("dialog", { name: "Record a payment" });
    await expect(pay).toBeVisible();
    await pay.getByRole("button", { name: "Cancel" }).click();
    await expect(pay).toHaveCount(0);
    await invoice.getByRole("button", { name: "Mark paid" }).click();
    await expect(pay.getByRole("button", { name: "Mark paid" })).toBeDisabled();
    await pay.getByLabel("Payment reference").fill("UTR-E2E-0001");
    await pay.getByRole("button", { name: "Mark paid" }).click();
    await expect(detail).toContainText("PAID");

    // Cancelling a contract asks first; Keep it changes nothing.
    await detail.getByRole("button", { name: "Cancel contract" }).click();
    const confirm = page.getByRole("alertdialog", { name: /Cancel contract/ });
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: "Keep the contract" }).click();
    await expect(detail).toContainText("ACTIVE");
    await detail.getByRole("button", { name: "Cancel contract" }).click();
    await confirm.getByRole("button", { name: "Cancel contract" }).click();
    await expect(detail).toContainText("CANCELLED");

    expect(prompts).toBe(0);
    await expectHealthyPage(page);
  });
});

test.describe("Billing speaks in the customer's words (F-187)", () => {
  test.use({ user: "C.owner" });

  test("limits and the usage breakdown name each meter, not its internal key", async ({ page }) => {
    await page.goto(org("C", "billing"));
    await expect(page.getByRole("heading", { name: "Limits", exact: true })).toBeVisible();
    const main = page.locator("main");
    // Before the fix: "*", "llm.input_token", "ocr.page", "Overage: allow_and_bill".
    for (const key of ["llm.input_token", "llm.output_token", "embedding.token", "ocr.page", "storage.gb_month"]) {
      await expect(main.getByText(key, { exact: true })).toHaveCount(0);
    }
    await expect(main).not.toContainText(/allow_and_bill|allow_and_warn|Overage: refuse/);
    await expect(main).toContainText("AI tokens in");
    await expect(main).toContainText("Total spend (all usage)");
    await expectHealthyPage(page);
  });
});

test.describe("Archiving an organization says how to undo it (F-191)", () => {
  test.use({ user: "C.owner" });

  test("the danger zone promises what the product does: the owner restores it from the picker", async ({ page }) => {
    await page.goto(org("C", "settings"));
    const zone = page.locator("section", { has: page.getByRole("heading", { name: "Danger zone" }) });
    await expect(zone).toBeVisible();
    // Before the fix: "Reactivation is a support request, not a button." and "Archive permanently",
    // although the owner restores an archived organization from the workspace picker (F-132).
    await expect(zone).not.toContainText(/support request|not a button/i);
    await expect(zone).toContainText(/restore/i);
    await zone.getByRole("button", { name: "Archive organization" }).click();
    await expect(zone.getByRole("button", { name: /permanently/i })).toHaveCount(0);
    await zone.getByRole("button", { name: "Cancel" }).click();
    await expectHealthyPage(page);
  });
});

test.describe("The Billing role on the Billing page (F-192)", () => {
  test.use({ user: "A.billing" });

  test("sees usage and limits, and is not offered what only the owner may change", async ({ page }) => {
    // Before the fix: three 403s (usage summary, series, limits) failed this test under the
    // strict fixture, "Manage payment method" and "Save limit" were offered and refused.
    await page.goto(org("A", "billing"));
    await expect(page.getByRole("heading", { name: "Limits", exact: true })).toBeVisible();
    await expect(page.locator("main")).toContainText("AI tokens in");
    await settle(page);
    await expect(page.getByRole("button", { name: "Manage payment method" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save limit" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Switch to / })).toHaveCount(0);
    await expect(page.locator("main")).toContainText(/owner/i);
    await expectHealthyPage(page);
  });
});

test.describe("Spend limits that are set stay listed (F-193)", () => {
  test.use({ user: "B.owner" });

  test("a saved limit is shown after a reload", async ({ page }) => {
    await page.goto(org("B", "billing"));
    const spendLimits = () => page.getByRole("heading", { name: "Spend limits", exact: true }).locator("xpath=ancestor::section[1]");
    const section = spendLimits();
    await section.getByLabel("Measure", { exact: true }).selectOption("ocr.page");
    await section.getByLabel("Period", { exact: true }).selectOption("DAY");
    const quantity = String(900_000 + Math.floor(Math.random() * 90_000));
    await section.getByLabel("Maximum quantity", { exact: true }).fill(quantity);
    await section.getByRole("checkbox", { name: /Stop work at the limit/ }).uncheck();
    await section.getByRole("button", { name: "Save limit" }).click();
    await expect(section).toContainText(Number(quantity).toLocaleString("en-US"));

    // Before the fix the limit lived only in the page's memory ("Set in this session") and the
    // page said "Configured limits can't be listed back yet, so note what you set".
    await page.reload();
    const limits = spendLimits();
    await expect(limits).toBeVisible();
    await expect(limits).not.toContainText("can't be listed back");
    await expect(limits).toContainText(Number(quantity).toLocaleString("en-US"));
    await expect(limits).toContainText("Document pages processed");
    await expectHealthyPage(page);
  });
});

test.describe("Billing opened by its address by a member (F-194)", () => {
  test.use({ user: "A.member" });

  test("a member is told the page is not theirs, and no billing request is made", async ({ page }) => {
    // Before the fix: "Loading billing…" for ever, five 403s behind it, and the line "You can
    // see usage and invoices" beneath. No 403 is allowed here: the page must not ask.
    await page.goto(org("A", "billing"));
    await expect(page.getByTestId("access-restricted")).toBeVisible();
    await expect(page.locator("main")).toContainText(/billing managers/i);
    await settle(page);
    await expectHealthyPage(page);
  });
});

test.describe("A failing request is not retried in a loop (F-195)", () => {
  test.use({ user: "C.owner" });

  test("when the subscription cannot be read, the Billing page asks a few times, not hundreds", async ({ page, problems }) => {
    problems.allowHttp(/\/billing\/subscription$/, [500], "an outage of this endpoint is simulated");
    problems.allowConsole(/status of 500/, "the simulated outage");
    let calls = 0;
    // A server outage on one endpoint (the only way to make the real API fail on demand).
    await page.route(/\/billing\/subscription$/, async (route) => {
      calls += 1;
      await route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"simulated outage"}' });
    });
    await page.goto(org("C", "billing"));
    await page.waitForTimeout(15_000);
    console.log(`subscription calls in 15 s: ${calls}`);
    // Before the fix the page unmounted its body for "Loading billing…" on every attempt, the
    // seat card re-mounted and asked again, for as long as the page was open (a 403 looped ~30
    // times a second; a 500 every few seconds, between retries), and it never said it had failed.
    expect(calls).toBeLessThanOrEqual(6);
    await expect(page.getByText("Loading billing…")).toHaveCount(0);
    await expect(page.locator("main")).toContainText(/couldn.t be loaded|could not be loaded/i);
  });
});
