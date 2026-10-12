/**
 * Campaign session 3 — the consoles people run the product from, as they are used.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api } from "../support/api";
import { org } from "../support/env";

test.describe("Audit log filters (F-311)", () => {
  test.use({ user: "C.owner" });

  // The filters were free text checked against exact codes: a lowercase or partial word showed
  // "Audit records could not be loaded", the actor filter needed an id the table never showed,
  // and Clear left the date box filled.
  test("an owner filters by action, by person and by date, and clears it all", async ({ page, session }) => {
    await page.goto(org("C", "audit"));
    const action = page.getByLabel("Action", { exact: true });
    await expect(action.locator("option").filter({ hasText: /^Updated$/ })).toHaveCount(1);
    await action.selectOption({ label: "Updated" });
    await expect(page.getByText(/could not be loaded/i)).toHaveCount(0);
    const actionCells = page.locator("tbody tr td:nth-child(3)");
    await expect(actionCells.first()).toBeVisible();
    for (const text of await actionCells.allInnerTexts()) {
      expect(text.trim()).toBe("Updated");
    }

    const actor = page.getByLabel("Actor", { exact: true });
    await actor.selectOption(String(session!.me.id));
    const me = (await actor.locator("option:checked").innerText()).trim();
    expect(me).not.toMatch(/^[0-9a-f-]{8,}$/);
    await expect(page.getByText(/could not be loaded/i)).toHaveCount(0);
    for (const text of await page.locator("tbody tr td:nth-child(2)").allInnerTexts()) {
      expect(text).toContain(me);
    }

    const from = page.getByLabel("From date", { exact: true });
    await from.fill("2026-01-01");
    await page.getByRole("button", { name: "Clear" }).click();
    await expect(from).toHaveValue("");
    await expect(action).toHaveValue("");
    await expect(page.getByRole("button", { name: "Clear" })).toHaveCount(0);
    await expectHealthyPage(page);
  });
});

test.describe("Unit economics (F-313)", () => {
  test.use({ user: "P.superadmin" });

  // The page's own rule: an unknown cost is never shown as a number. The supplier table printed
  // "$0.00" for a supplier none of whose events had a cost basis.
  test("a supplier whose cost is unknown reads 'unknown', not $0.00", async ({ page }) => {
    await page.route(/\/admin\/cogs\/margins\/providers(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          period_start: "2026-09-12T00:00:00Z", period_end: "2026-10-12T00:00:00Z",
          entries: [
            { provider: "groq", cost_basis_micros: 0, revenue_micros: 12_000_000, event_count: 40, unknown_cost_event_count: 40 },
            { provider: "gemini", cost_basis_micros: 2_500_000, revenue_micros: 9_000_000, event_count: 10, unknown_cost_event_count: 0 },
          ],
        }),
      }),
    );
    await page.goto("/admin/margins");
    await expect(page.getByRole("heading", { name: "Modelled cost by supplier" })).toBeVisible();
    const unknown = page.getByRole("row", { name: /^groq / });
    await expect(unknown).toContainText(/unknown/i);
    await expect(unknown).not.toContainText("$0.00");
    await expect(page.getByRole("row", { name: /^gemini \$/ })).toContainText("$2.50");
  });
});

test.describe("Unit economics refresh (F-316)", () => {
  test.use({ user: "P.superadmin" });

  // Refresh re-asked for the window computed when the page opened, so a page left open all day
  // showed yesterday's "last 30 days" after every Refresh.
  test("Refresh asks for the window ending now", async ({ page }) => {
    const ends: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname.endsWith("/admin/cogs/margins/summary")) {
        ends.push(url.searchParams.get("period_end") ?? "");
      }
    });
    await page.goto("/admin/margins");
    await expect.poll(() => ends.length).toBeGreaterThan(0);
    const first = ends[ends.length - 1]!;
    await page.waitForTimeout(1_500);
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect.poll(() => ends.length).toBeGreaterThan(1);
    expect(Date.parse(ends[ends.length - 1]!)).toBeGreaterThan(Date.parse(first) + 1_000);
  });
});

test.describe("Platform consoles say when a list could not be loaded (F-314)", () => {
  test.use({ user: "P.superadmin" });

  // Each list read only "is it empty", so a failed request showed "No contracts.", "Nothing
  // refused in this window." or "No metered usage in this period.": an outage read as a fact.
  test("a failed list is reported as a failure, never as an empty list", async ({ page, problems }) => {
    problems.allowHttp(/\/admin\//, [500], "these lists are made to fail");
    await page.route(/\/admin\/revops\/(contracts|promo-codes|price-books)(\?|$)/, (route) => route.fulfill({ status: 500, body: "{}" }));
    await page.route(/\/admin\/sovereign\/refusals(\?|$)/, (route) => route.fulfill({ status: 500, body: "{}" }));
    await page.route(/\/admin\/cogs\/margins\/tenants(\?|$)/, (route) => route.fulfill({ status: 500, body: "{}" }));

    const cases: [string, string, RegExp][] = [
      ["/admin/revops?tab=contracts", "No contracts.", /contracts could not be loaded/i],
      ["/admin/revops?tab=promos", "No promo codes.", /promo codes could not be loaded/i],
      ["/admin/revops?tab=books", "No price book yet.", /price books could not be loaded/i],
      ["/admin/sovereign?tab=egress", "Nothing refused in this window.", /refused connections could not be loaded/i],
      ["/admin/margins", "No metered usage in this period.", /tenants could not be loaded/i],
    ];
    for (const [path, empty, failed] of cases) {
      await page.goto(path);
      await expect(page.getByText(failed)).toBeVisible({ timeout: 30_000 });
      await expect(page.getByText(empty, { exact: true })).toHaveCount(0);
    }
  });
});

test.describe("RevOps price books (F-315)", () => {
  test.use({ user: "P.superadmin" });

  // A price set by mistake in a draft book could not be removed (the route existed, the page had
  // no control), and Publish (irreversible: the book is frozen and the one in force retired) ran
  // on one click. Publishing is not completed here: it would change the prices other tests read.
  test("a draft's price can be removed, and publishing asks first, naming what it retires", async ({ page, session }) => {
    const code = `E2E-${Date.now().toString(36)}`.toUpperCase();
    const book = await api<{ id: string }>(session, "POST", "/admin/revops/price-books", { code, currency: "USD", notes: null });
    expect(book.status, book.text).toBe(201);
    for (const interval of ["month", "year"]) {
      const set = await api(session, "PUT", `/admin/revops/price-books/${book.body.id}/entries`, {
        tier_key: "business", interval, unit_amount: interval === "month" ? 2900 : 29000, gateway_price_id: null,
      });
      expect(set.status, set.text).toBe(200);
    }
    await page.goto("/admin/revops?tab=books");
    await page.getByRole("button", { name: new RegExp(`^${code} ·`) }).click();
    await page.getByRole("button", { name: "Remove business year" }).click();
    await expect(page.getByRole("button", { name: "Remove business year" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Remove business month" })).toBeVisible();

    await page.getByRole("button", { name: "Publish", exact: true }).click();
    const confirm = page.getByRole("alertdialog", { name: new RegExp(`Publish ${code}`) });
    await expect(confirm).toContainText(/cannot be changed/i);
    await expect(confirm).toContainText(/retired|first USD price book/i);
    await confirm.getByRole("button", { name: "Keep the draft" }).click();
    await expect(page.getByRole("heading", { name: new RegExp(`${code} · USD · DRAFT`) })).toBeVisible();
    await expectHealthyPage(page);
  });
});
