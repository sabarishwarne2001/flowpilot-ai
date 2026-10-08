/**
 * PHASE 2 (enterprise processing), live defects found by driving the running stack.
 *
 * F-171: a matching case that compared no line at all was MATCHED, and "Approve match" approved it
 *        on no evidence.
 * F-172: the matching queue printed every variance in rupees (the currency was a constant).
 * F-173: INV-E2E-1002 asks to be paid into a new account and Payment risk said no invoice changed
 *        its bank account: the check did not read `vendor_bank_account`. (12-processing's test of the
 *        same name only checked that the radar was not empty, which the duplicate finding satisfied.)
 * F-174: the queue and the radar showed the internal vendor key ("name:acme industrial supplies").
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { ws } from "../support/env";

test.describe("Three-way matching (F-171, F-172, F-174)", () => {
  test.use({ user: "C.owner" });

  test("the queue names the vendor as printed and states amounts in the case's currency", async ({ page }) => {
    await page.goto(ws("C", "procurement"));
    await settle(page);
    const table = page.locator("main table");
    await expect(table).toBeVisible();
    await expect(table).toContainText("Acme Industrial Supplies Ltd");
    await expect(table).not.toContainText("name:acme");
    // The sample documents are in US dollars: no rupee sign anywhere on the page.
    await expect(page.locator("main")).not.toContainText("₹");
    await expect(table).toContainText("$");
    // Each row says which documents were matched, by the numbers they print.
    await expect(table).toContainText("INV-E2E-1001");
    await expect(table).toContainText("PO-E2E-5001");
    await expectHealthyPage(page);
  });

  test("a case that compared no line asks for a reason before it can be approved", async ({ page }) => {
    await page.goto(ws("C", "procurement"));
    await settle(page);
    const row = page.locator("main tbody tr").filter({ hasText: "INV-E2E-1001" }).first();
    await row.getByRole("link", { name: "Review" }).click();
    await expect(page.getByRole("heading", { name: /Three-way match/ })).toBeVisible();
    await expect(page.getByRole("region", { name: "Documents in this case" })).toContainText("PO-E2E-5001");
    await page.getByRole("button", { name: "Approve match" }).click();
    // No approval happens on the click: the reason dialog opens instead.
    await expect(page.getByRole("heading", { name: "Approve without a line-by-line comparison" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Override reason" })).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.locator("main")).not.toContainText(/approved/i);
  });
});

test.describe("Forensic audit radar (F-173, F-174)", () => {
  test.use({ user: "C.owner" });

  test("the new payee account on INV-E2E-1002 is a payment-risk flag", async ({ page }) => {
    await page.goto(ws("C", "radar"));
    await settle(page);
    const panel = page.locator("section, div").filter({ has: page.getByText("Payment risk", { exact: true }) }).first();
    await expect(panel).not.toContainText("No invoice changed its bank account");
    // Only the last four characters of either account are ever shown.
    await expect(page.locator("main")).toContainText("•••• 3459");
    await expect(page.locator("main")).toContainText("•••• 6819");
  });

  test("a duplicate's vendor evidence reads as a name, not a key", async ({ page }) => {
    await page.goto(ws("C", "radar"));
    await settle(page);
    const finding = page.getByText(/carries the same document number/).first();
    await expect(finding).toBeVisible();
    await finding.click();
    await expect(page.locator("main")).toContainText("Acme Industrial Supplies");
    await expect(page.locator("main")).not.toContainText("name:acme");
  });
});
