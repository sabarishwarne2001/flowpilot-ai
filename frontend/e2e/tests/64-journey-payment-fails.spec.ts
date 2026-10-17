/**
 * JOURNEY 5 — a payment fails, dunning runs, access is restricted, the card is fixed and access
 * returns (campaign session 4, §D; N-072).
 *
 * A Developer customer's renewal is declined. The owner is emailed at each failed retry and the
 * app says so at once; when the gateway's retries run out the subscription is marked unpaid and
 * the organization becomes read-only (reading still works, writes are refused with the reason).
 * The owner updates the card in the billing portal, the open invoice is paid, and everything is
 * back at once.
 *
 * On Dodo (the default) the stand-in Dodo declines the renewal and its webhooks reach Servabo's
 * real endpoint and worker; the subscription is on hold for a grace window, and the clock is moved
 * through it (`e2e_stand_in.py payment-fails-again` / `mark-unpaid`) so the hourly reminders, the
 * final notice and read-only come due. The owner then updates the card in the stand-in Dodo's
 * customer portal, reached from Servabo's own "Update payment method" button.
 *
 * On Stripe (E2E_GATEWAY=stripe) the gateway's side (the declined renewal, its retries, the card
 * fixed) is played by `backend/scripts/e2e_stand_in.py` through Servabo's own reconcile code; the
 * portal page itself is the gateway's and is stood in at the network edge.
 */
import type { Page } from "@playwright/test";

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api } from "../support/api";
import { DODO_ORIGIN, GATEWAY } from "../support/env";
import { waitForMail } from "../support/mail";
import { documents, invoicePdf, newCustomer, signInAs, standIn, uploadInBrowser } from "../support/journey";

async function portalStandsIn(page: Page): Promise<{ opened: number }> {
  const calls = { opened: 0 };
  await page.route(/\/billing\/portal-session/, async (route) => {
    calls.opened += 1;
    await route.fulfill({ json: { url: new URL("/__billing-portal__", page.url()).toString() } });
  });
  await page.route(/__billing-portal__/, (route) =>
    route.fulfill({ contentType: "text/html", body: "<title>Billing portal</title><h1>Update your card</h1>" }),
  );
  return calls;
}

test.describe("Journey: a payment fails and is fixed", () => {
  test.setTimeout(300_000);

  test("declined renewal → emails → read-only → card fixed → everything back", async ({ page }) => {
    const customer = await newCustomer("dunning-journey", "Dunning Journey Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "developer", "--seats", "1");
    await signInAs(page, customer.email);
    const portal = GATEWAY === "stripe" ? await portalStandsIn(page) : null;
    await uploadInBrowser(page, customer, invoicePdf("dunning-before"));
    await expect.poll(async () => (await documents(customer)).length, { timeout: 60_000 }).toBe(1);

    // The renewal is declined: the owner is emailed, and the app says so straight away.
    let since = Date.now() - 1_000;
    standIn("renewal-fails", customer.orgSlug);
    await waitForMail(customer.email, GATEWAY === "dodo" ? /Payment failed/ : /Payment could not be processed/, since, 90_000);
    await page.goto(customer.ws());
    const notice = page.getByRole("status").filter({ hasText: "Payment failed." });
    await expect(notice).toBeVisible({ timeout: 15_000 });
    await expect(notice.getByRole("button", { name: "Update payment method" })).toBeVisible();

    // Each further failed retry is another email; access is unchanged meanwhile.
    since = Date.now() - 1_000;
    standIn("payment-fails-again", customer.orgSlug);
    await waitForMail(customer.email, /Payment still outstanding/, since, 90_000);
    standIn("payment-fails-again", customer.orgSlug);
    await waitForMail(customer.email, /Final notice before access changes/, since, 90_000);

    // The retries run out and the gateway marks the subscription unpaid: read-only.
    standIn("mark-unpaid", customer.orgSlug);
    await page.goto(customer.ws("work-items"));
    const alert = page.getByRole("alert").filter({ hasText: "Your account is read-only until payment is resolved" });
    await expect(alert).toBeVisible({ timeout: 15_000 });
    await expect(alert).toContainText("Nothing has been deleted");
    await expect(page.getByRole("table").getByText(/dunning-before/)).toBeVisible();
    const refused = await api(customer.session, "POST", `/workspaces/${customer.wsId}/assistant/conversations`,
      { title: "While unpaid" });
    expect(refused.status, refused.text).toBe(402);
    expect(refused.text).toContain("BILLING_READ_ONLY");

    // The owner fixes the card in the billing portal; the gateway retries and the invoice is paid.
    await alert.getByRole("button", { name: "Update payment method" }).click();
    if (GATEWAY === "dodo") {
      await expect(page).toHaveURL(new RegExp(`^${DODO_ORIGIN.replace(/\./g, "\\.")}/portal/`), { timeout: 20_000 });
      await page.getByRole("button", { name: "Update card" }).click();
      await expect(page.getByRole("status")).toContainText("Card updated");
      await expect.poll(async () => (await api<{ state: string }>(customer.session, "GET",
        `/organizations/${customer.orgId}/billing/access-summary`)).body.state, { timeout: 60_000 }).toBe("ACTIVE");
    } else {
      await expect(page).toHaveURL(/__billing-portal__/);
      expect(portal?.opened).toBe(1);
      standIn("card-fixed", customer.orgSlug);
    }
    await page.goto(customer.ws());
    await expect(page.getByRole("alert").filter({ hasText: "read-only" })).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByRole("status").filter({ hasText: "Payment failed." })).toHaveCount(0);
    await uploadInBrowser(page, customer, invoicePdf("dunning-after"));
    await expect.poll(async () => (await documents(customer)).length, { timeout: 60_000 }).toBe(2);
    await expectHealthyPage(page);
  });
});
