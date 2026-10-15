/**
 * HELM: importing a bank statement (owner addition A), in the browser.
 *
 * A Business customer uploads INV-E2E-1001 and 1002 (Acme Industrial Supplies, USD 1,250 each). The
 * radar flags one as a probable duplicate and the other's bank account as changed (which way round
 * depends on the order the worker reads them); the owner verifies the account by phone and
 * dismisses that flag. Helm plans to pay the invoice that is not a duplicate and the owner approves
 * it, so it is on the payment run. The owner then adds the bank account (only its last four digits
 * are kept) and imports a CSV export:
 *
 *   the payment to Acme naming the scheduled invoice, on the run day -> Helm matches it by itself;
 *   a wire of USD 1,250 with no name                                  -> suggested: the other one;
 *   a monthly fee                                                     -> no invoice: set aside.
 *
 * The owner confirms the suggestion, sets the fee aside, and reopens Helm's own match: its payment
 * record is undone and the line waits again. The closing balance is the account's cash.
 */
import { test, expect, settle } from "../support/fixtures";
import { api, listWorkItems, uploadFile } from "../support/api";
import { newCustomer, signInAs, standIn, type Customer } from "../support/journey";
import { samplePath } from "../support/sample-docs";

const NEEDS_MODEL = "needs extracted invoice fields: run with E2E_LLM=1 (support/llm-mock.mjs)";
const TERMINAL = new Set(["COMPLETED", "FAILED", "ERROR", "NEEDS_REVIEW", "REVIEW", "PENDING_REVIEW"]);

let customer: Customer;
let bookId: string;
let runDate: string;
/** The invoice Helm scheduled, and the other one. */
let paid: string;
let other: string;

interface Action {
  readonly id: string;
  readonly title: string;
  readonly guard_passed: boolean;
  readonly state: string;
  readonly kind: string;
  readonly run_date: string | null;
  readonly work_item_id: string | null;
}

test.describe("Helm bank statements — import, match, and a person decides the rest", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test.beforeAll(async () => {
    test.skip(process.env.E2E_LLM !== "1", NEEDS_MODEL);
    test.setTimeout(300_000);
    customer = await newCustomer("helmbank", "Helm Bank Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "business", "--seats", "3");
    for (const sample of ["invoice1001", "invoice1002"] as const) {
      const upload = await uploadFile(customer.session, customer.wsId, samplePath(sample));
      expect(upload.status, upload.text.slice(0, 300)).toBe(201);
    }
    await expect
      .poll(async () => {
        const items = await listWorkItems(customer.session, customer.wsId);
        return items.length === 2 && items.every((item) => TERMINAL.has(String(item.status).toUpperCase()));
      }, { timeout: 240_000, intervals: [2_000, 3_000] })
      .toBe(true);
    const base = `/organizations/${customer.orgId}/helm`;
    const book = await api<{ id: string }>(customer.session, "POST", `${base}/books`, { workspace_id: customer.wsId });
    expect(book.status, book.text.slice(0, 300)).toBe(201);
    bookId = book.body.id;
    const settings = await api(customer.session, "PATCH", `${base}/books/${bookId}/settings`, {
      identities: [{ name: "Caretakers Global Inc", tax_id: null }],
    });
    expect(settings.status, settings.text.slice(0, 300)).toBe(200);
    const mandate = await api(customer.session, "POST", `${base}/books/${bookId}/mandates`, {
      template: "pay_on_time",
      activate: true,
    });
    expect(mandate.status, mandate.text.slice(0, 300)).toBe(201);
    // The owner verifies the changed bank account by phone: the flag is dismissed, as on the radar page.
    await expect
      .poll(async () => {
        const risk = await api<{ items: Array<{ id: string; kind: string; status: string }> }>(
          customer.session, "GET", `/workspaces/${customer.wsId}/payment-risk`);
        return (risk.body.items ?? []).some((f) => f.kind === "BANK_ACCOUNT_CHANGED");
      }, { timeout: 60_000, intervals: [1_000, 2_000] })
      .toBe(true);
    const risk = await api<{ items: Array<{ id: string; kind: string; status: string }> }>(
      customer.session, "GET", `/workspaces/${customer.wsId}/payment-risk`);
    for (const flag of risk.body.items.filter((f) => f.kind === "BANK_ACCOUNT_CHANGED" && f.status === "OPEN")) {
      const dismissed = await api(customer.session, "POST", `/workspaces/${customer.wsId}/payment-risk/${flag.id}/dismiss`,
        { reason: "Verified the new account by phone with Acme" });
      expect(dismissed.status, dismissed.text.slice(0, 300)).toBe(200);
    }
    // Plan, then approve the payment the Guard passes: it goes on the payment run.
    let pay: Action | undefined;
    await expect
      .poll(async () => {
        const planned = await api<{ actions: Action[] }>(customer.session, "POST", `${base}/books/${bookId}/plan`);
        pay = planned.body.actions?.find((a) => a.kind === "schedule_payment" && a.guard_passed && /Acme/.test(a.title));
        return Boolean(pay);
      }, { timeout: 90_000, intervals: [2_000, 3_000] })
      .toBe(true);
    runDate = (pay as Action).run_date as string;
    const numbers = new Map<string, string>();
    for (const item of await listWorkItems(customer.session, customer.wsId)) {
      const found = /INV-E2E-\d+/.exec(String(item.original_filename ?? ""));
      if (found) numbers.set(String(item.id), found[0]);
    }
    paid = numbers.get(String((pay as Action).work_item_id)) as string;
    other = paid === "INV-E2E-1001" ? "INV-E2E-1002" : "INV-E2E-1001";
    expect(paid).toMatch(/INV-E2E-100[12]/);
    const approved = await api(customer.session, "POST", `${base}/actions/${(pay as Action).id}/approve`);
    expect(approved.status, approved.text.slice(0, 300)).toBe(200);
    await expect
      .poll(async () => {
        const run = await api<{ lines?: unknown[]; runs?: unknown[] }>(customer.session, "GET", `${base}/books/${bookId}/payment-run`);
        return run.text.includes(paid);
      }, { timeout: 60_000, intervals: [1_000, 2_000] })
      .toBe(true);
  });

  test("the owner imports a CSV; Helm matches the planned payment and the owner decides the rest", async ({ page }) => {
    test.skip(process.env.E2E_LLM !== "1", NEEDS_MODEL);
    await signInAs(page, customer.email);
    await page.goto(`/${customer.orgSlug}/helm/bank?book=${bookId}`);
    await settle(page);
    await expect(page.getByRole("heading", { name: "Bank statements", level: 1 })).toBeVisible();

    await page.getByRole("textbox", { name: "Account name" }).fill("Chase operating");
    await page.getByRole("textbox", { name: "Account number" }).fill("0001-2345-6789");
    await page.getByRole("textbox", { name: "Currency" }).fill("USD");
    await page.getByRole("button", { name: "Add account" }).click();
    await expect(page.getByTestId("helm-bank-account")).toContainText("Chase operating ····6789");

    const csv = [
      "Chase Business Checking",
      "Date,Description,Reference,Debit,Credit,Balance",
      `${runDate},ACH ACME INDUSTRIAL SUPPLIES ${paid},ACH1,1250.00,,18750.00`,
      `${runDate},WIRE OUT 77812,W2,1250.00,,17500.00`,
      `${runDate},MONTHLY SERVICE FEE,F3,15.00,,17485.00`,
    ].join("\n");
    const previewed = page.waitForResponse((r) => r.url().endsWith("/bank-statements/preview"));
    await page.getByLabel("Statement file").setInputFiles({ name: "chase-oct.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
    expect((await previewed).status()).toBe(200);
    const preview = page.getByTestId("helm-bank-preview");
    await expect(preview.getByRole("combobox", { name: "Money out" })).toHaveValue("Debit");
    await expect(preview.getByRole("table")).toContainText(`ACH ACME INDUSTRIAL SUPPLIES ${paid}`);

    const imported = page.waitForResponse((r) => r.url().endsWith("/bank-statements") && r.request().method() === "POST");
    await preview.getByRole("button", { name: "Import this statement" }).click();
    expect((await imported).status()).toBe(201);
    await expect(page.getByTestId("helm-bank-result")).toContainText("3 new lines; Helm matched 1; 2 for you to check.");

    // The wire: suggested, with its reasons; the owner confirms it paid 1002.
    const wire = page.getByTestId("helm-bank-line").filter({ hasText: "WIRE OUT 77812" });
    await expect(wire).toContainText("the amount is exactly");
    await wire.getByRole("button", { name: new RegExp(`It paid ${other}`) }).click();
    await expect(page.getByTestId("helm-bank-line").filter({ hasText: "WIRE OUT 77812" })).toHaveCount(0);

    // The fee is not an invoice payment.
    const fee = page.getByTestId("helm-bank-line").filter({ hasText: "MONTHLY SERVICE FEE" });
    await expect(fee).toContainText("No open invoice has this amount.");
    await fee.getByRole("button", { name: "Not an invoice payment" }).click();
    await fee.getByRole("textbox", { name: "What is this line?" }).fill("Bank fee");
    await fee.getByRole("button", { name: "Not an invoice payment" }).click();
    await expect(page.getByText("Nothing waits for you.")).toBeVisible();

    const done = page.getByTestId("helm-bank-matched");
    await expect(done.filter({ hasText: paid })).toContainText("(Helm");
    await expect(done.filter({ hasText: other })).toContainText("(a person");
    await expect(done.filter({ hasText: "MONTHLY SERVICE FEE" })).toContainText("Not an invoice payment: Bank fee");
    // Helm's match is recorded by the worker, with a certificate.
    await expect(done.filter({ hasText: paid })).toContainText("recorded", { timeout: 60_000 });

    // Reopening Helm's match undoes its payment record; the line waits again.
    await done.filter({ hasText: paid }).getByRole("button", { name: /^Reopen the line/ }).click();
    await expect(page.getByTestId("helm-bank-line").filter({ hasText: paid })).toBeVisible();

    // The closing balance is the account's cash.
    await expect(page.getByTestId("helm-bank-account")).toContainText("17,485.00");
  });
});
