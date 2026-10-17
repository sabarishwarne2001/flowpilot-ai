/**
 * JOURNEY 9 — a customer's plan, invoices, refund and cancellation on Dodo Payments (website and
 * payments session, C1).
 *
 * A Developer customer upgrades to Business from Servabo's own plan screen: Dodo's change-plan
 * preview gives the exact amount charged today, the owner confirms that amount, and the charge
 * becomes an invoice issued by Dodo (the merchant of record) that the owner downloads. A downgrade
 * is scheduled for the renewal and withdrawn. Dodo refunds the upgrade charge and the invoice says
 * so. Finally the owner cancels in Dodo's customer portal, and Servabo shows the plan ending at the
 * period's end.
 *
 * Everything on Dodo's side is the stand-in Dodo (`backend/tests/fakes/fake_dodo.py`, started by
 * start-stack.sh): Servabo's own API calls reach it, and its signed webhooks reach Servabo's real
 * endpoint and worker. The refund is Dodo's action, played by `e2e_stand_in.py refund`.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api } from "../support/api";
import { DODO_ORIGIN, GATEWAY } from "../support/env";
import { waitForMail } from "../support/mail";
import { newCustomer, signInAs, standIn } from "../support/journey";

interface InvoiceRow {
  number: string;
  total_micros: number;
  amount_refunded_micros?: number;
  issued_by?: string | null;
}

async function invoices(customer: Awaited<ReturnType<typeof newCustomer>>): Promise<InvoiceRow[]> {
  const listed = await api<{ invoices?: InvoiceRow[] }>(customer.session, "GET", `/organizations/${customer.orgId}/invoices`);
  return listed.body.invoices ?? [];
}

test.describe("Journey: plan, invoices, refund and cancellation on Dodo", () => {
  test.skip(GATEWAY !== "dodo", "Dodo's own plan change, invoices and portal (E2E_GATEWAY=stripe runs the Stripe journeys)");
  test.setTimeout(360_000);

  test("upgrade at Dodo's price → invoice downloaded → downgrade scheduled and withdrawn → refund → cancel in the portal",
    async ({ page }) => {
      const customer = await newCustomer("dodo-journey", "Dodo Journey Ltd");
      standIn("subscribe", customer.orgSlug, "--plan", "developer", "--seats", "1");
      await signInAs(page, customer.email);
      await page.goto(customer.org("billing"));
      await expect(page.locator("main")).toContainText("You're on Developer", { timeout: 20_000 });

      // The upgrade: Dodo prices the rest of the period, the owner confirms that exact amount.
      await page.getByTestId("plan-cta-business").click();
      const preview = page.getByTestId("plan-change-preview");
      await expect(preview).toBeVisible({ timeout: 20_000 });
      const amount = (await preview.getByTestId("plan-change-amount").innerText()).trim();
      expect(amount).toMatch(/^\$\d/);
      await preview.getByRole("button", { name: `Upgrade and pay ${amount}` }).click();
      await expect(page.getByText("You're on Business now.")).toBeVisible({ timeout: 20_000 });
      await expect(page.locator("main")).toContainText("You're on Business", { timeout: 20_000 });

      // The upgrade's charge is invoiced: issued by Dodo, downloadable.
      await expect.poll(async () => (await invoices(customer)).length, { timeout: 60_000 }).toBe(2);
      await page.reload();
      const invoiceList = page.locator("section").filter({ has: page.getByRole("heading", { name: "Invoices" }) });
      await expect(invoiceList.getByText("Tax invoice issued by Dodo Payments (merchant of record).").first()).toBeVisible();
      const downloaded = page.waitForEvent("download");
      await invoiceList.getByRole("button", { name: "Download invoice" }).first().click();
      expect((await downloaded).suggestedFilename()).toMatch(/\.pdf$/);

      // A downgrade waits for the renewal; the owner withdraws it.
      await page.getByTestId("plan-cta-developer").click();
      await expect(page.getByTestId("plan-change-date")).toBeVisible({ timeout: 20_000 });
      await page.getByRole("button", { name: /^Schedule the change for / }).click();
      const scheduled = page.getByTestId("scheduled-plan-change");
      await expect(scheduled).toContainText("Changing to Developer", { timeout: 20_000 });
      await scheduled.getByRole("button", { name: "Keep the current plan" }).click();
      await expect(scheduled).toHaveCount(0, { timeout: 20_000 });

      // Dodo refunds the upgrade's charge: the invoice says so, and the owner is told.
      const since = Date.now() - 1_000;
      standIn("refund", customer.orgSlug);
      await expect.poll(async () => (await invoices(customer)).filter((i) => (i.amount_refunded_micros ?? 0) > 0).length,
        { timeout: 60_000 }).toBe(1);
      await waitForMail(customer.email, /Refund of/, since, 90_000);
      await page.reload();
      await expect(invoiceList.getByText("Refunded", { exact: true })).toBeVisible({ timeout: 20_000 });

      // The owner cancels in Dodo's portal: the plan runs to the end of the period.
      await page.getByRole("button", { name: "Manage payment method" }).click();
      await expect(page).toHaveURL(new RegExp(`^${DODO_ORIGIN.replace(/\./g, "\\.")}/portal/`), { timeout: 20_000 });
      await page.getByRole("button", { name: "Cancel subscription" }).click();
      await expect(page.getByRole("status")).toContainText("will end at the next billing date");
      await expect.poll(async () => {
        const state = await api<{ subscription?: { cancel_at_period_end?: boolean } | null }>(customer.session, "GET",
          `/organizations/${customer.orgId}/billing/subscription`);
        return state.body.subscription?.cancel_at_period_end ?? false;
      }, { timeout: 60_000 }).toBe(true);
      await page.goto(customer.org("billing"));
      await expect(page.locator("main")).toContainText("cancels at the end of the period", { timeout: 20_000 });
      await expectHealthyPage(page);
    });
});
