/**
 * IN-APP PLAN CHANGE (N-050, campaign session 4): a paying customer changes plans in the
 * payment gateway's billing portal, which moves the live subscription and prorates. A second
 * checkout would create, and charge, a second subscription (F-253; the server refuses it with
 * 409). This proves every place that offers a paying customer a plan change ends in the portal
 * and never starts a checkout: the plan card's button, picking a plan in the list, choosing Free,
 * and the "View plans" route from a locked feature.
 *
 * Sandbox: no Stripe key, so the plans answer says checkout is unavailable and the portal cannot
 * be reached. Only those two edges are stood in for: the plans answer is the server's own with
 * `checkout_available` set, the portal and checkout requests are intercepted and counted, and
 * the portal's address is a local page. Each portal request is read: a picked plan and its seats
 * travel with it, and the portal opens on the confirmation of that change (F-344).
 */
import type { Page } from "@playwright/test";

import { test, expect } from "../support/fixtures";
import { org, ws } from "../support/env";

interface GatewayCalls {
  portal: number;
  checkout: number;
  /** What each portal request asked the portal to open on (F-344). */
  picks: Array<{ quota_tier_key?: string; seats?: number }>;
}

async function standInForTheGateway(page: Page): Promise<GatewayCalls> {
  const calls: GatewayCalls = { portal: 0, checkout: 0, picks: [] };
  await page.route(/\/api\/v1\/organizations\/[^/]+\/billing\/plans/, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, json: { ...body, checkout_available: true, checkout_unavailable_reason: null } });
  });
  await page.route(/\/billing\/portal-session/, async (route) => {
    calls.portal += 1;
    calls.picks.push((route.request().postDataJSON() ?? {}) as GatewayCalls["picks"][number]);
    await route.fulfill({ json: { url: new URL("/__billing-portal__", page.url()).toString() } });
  });
  await page.route(/\/billing\/checkout-session/, async (route) => {
    calls.checkout += 1;
    await route.fulfill({ status: 500, json: { detail: "a subscriber must never reach checkout" } });
  });
  await page.route(/__billing-portal__/, (route) =>
    route.fulfill({ contentType: "text/html", body: "<title>Billing portal</title><h1>Billing portal</h1>" }),
  );
  return calls;
}

async function expectThePortal(page: Page, calls: GatewayCalls): Promise<void> {
  await expect(page).toHaveURL(/__billing-portal__/);
  expect({ portal: calls.portal, checkout: calls.checkout }).toEqual({ portal: 1, checkout: 0 });
}

test.describe("A paying customer changes plans in the billing portal", () => {
  test.use({ user: "A.owner" });

  test("the plan card's button opens the portal", async ({ page }) => {
    const calls = await standInForTheGateway(page);
    await page.goto(org("A", "billing"));
    const cta = page.getByTestId("plan-cta-business");
    await expect(cta).toHaveText(/Upgrade to Business in the billing portal/);
    await cta.click();
    await expectThePortal(page, calls);
    // F-344: the portal opens on Business with the seats the subscription holds.
    expect(calls.picks[0]?.quota_tier_key).toBe("business");
    expect(calls.picks[0]?.seats).toBeGreaterThanOrEqual(1);
  });

  test("picking a plan in the list continues in the portal, not to payment", async ({ page }) => {
    const calls = await standInForTheGateway(page);
    await page.goto(org("A", "billing"));
    await page.getByRole("radio", { name: /Business/ }).check();
    // F-344: the seats picked here are the seats the portal confirms.
    const seats = page.getByRole("spinbutton", { name: /Seats/ });
    const more = Number(await seats.inputValue()) + 2;
    await seats.fill(String(more));
    await page.getByRole("button", { name: "Change to this plan" }).click();
    await expect(page.getByText(`with ${more} seats and prorates the difference`)).toBeVisible();
    await page.getByRole("button", { name: "Continue in the billing portal" }).click();
    await expectThePortal(page, calls);
    expect(calls.picks[0]).toMatchObject({ quota_tier_key: "business", seats: more });
  });

  test("moving to Free is a cancellation in the portal", async ({ page }) => {
    const calls = await standInForTheGateway(page);
    await page.goto(org("A", "billing"));
    await page.getByRole("radio", { name: /^Free/ }).check();
    await page.getByRole("button", { name: "Use this plan" }).click();
    await expect(page.getByText(/cancel your subscription in the billing portal/i)).toBeVisible();
    await page.getByRole("button", { name: "Continue in the billing portal" }).click();
    await expectThePortal(page, calls);
    expect(calls.picks[0]?.quota_tier_key).toBeUndefined();  // a cancellation, on the portal's home page
  });

  test("a locked feature's View plans leads to the same portal", async ({ page }) => {
    const calls = await standInForTheGateway(page);
    await page.goto(ws("A", "notifications"));
    await page.getByRole("button", { name: "Cases (not included in your plan)" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "View plans" }).click();
    await expect(page).toHaveURL(/\/billing/);
    await page.getByTestId("plan-cta-business").click();
    await expectThePortal(page, calls);
  });
});
