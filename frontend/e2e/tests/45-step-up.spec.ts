/**
 * Campaign session 3 (F-117) — the "Confirm it's you" step-up dialog.
 *
 * Opening the billing portal needs a credential presented in the last few minutes
 * (`portal_service.assert_recent_authentication`, proven by the server tests). When the server
 * asks for one (403 with `WWW-Authenticate: Bearer error="reauth_required"`), the app asks for
 * the password, signs in again for real (`POST /auth/login` restamps `auth_time`; a refresh would
 * not), and replays the action. Dismissing the dialog fails closed: nothing is replayed.
 *
 * A fresh test session is always recent, so the server's challenge is answered here for the
 * portal request until the person has confirmed; the sign-in is real. The portal itself is a
 * payment provider's page, so its address is answered too (no live payment endpoint is called).
 */
import type { Page, Route } from "@playwright/test";

import { test, expect } from "../support/fixtures";
import { PASSWORD, org } from "../support/env";

const PORTAL = /\/billing\/portal-session$/;

const challengeUntilConfirmed = async (page: Page): Promise<{ calls: () => number }> => {
  let calls = 0;
  let confirmed = false;
  page.on("response", (response) => {
    if (/\/auth\/login$/.test(response.url()) && response.status() === 200) {
      confirmed = true;
    }
  });
  await page.route(/\/billing\/(subscription|access-summary)$/, async (route: Route) => {
    // The button shows for an organization with a billing account.
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, has_billing_account: true } });
  });
  await page.route(PORTAL, (route) => {
    calls += 1;
    if (!confirmed) {
      return route.fulfill({
        status: 403,
        headers: { "WWW-Authenticate": 'Bearer error="reauth_required"', "Content-Type": "application/json" },
        body: JSON.stringify({ detail: "Confirm it's you to manage billing." }),
      });
    }
    return route.fulfill({ status: 200, json: { url: new URL("/portal-opened", page.url()).toString(), expires_at: null } });
  });
  return { calls: () => calls };
};

test.describe("Step-up: confirm it's you", () => {
  test.use({ user: "C.owner" });

  test("cancelling fails closed; a wrong password is refused; the right one replays the action", async ({ page, problems }) => {
    problems.allowHttp(PORTAL, [403], "the server asks for a recent sign-in");
    problems.allowHttp(/\/auth\/login$/, [401], "a wrong password is refused");
    const portal = await challengeUntilConfirmed(page);
    await page.goto(org("C", "billing"));
    const open = page.getByRole("button", { name: /manage payment method/i });
    const dialog = page.getByRole("dialog", { name: /confirm it.s you/i });

    await open.click();
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await page.waitForTimeout(500);
    expect(portal.calls(), "dismissing replays nothing").toBe(1);

    await open.click();
    await expect(dialog).toBeVisible();
    await dialog.locator("#stepup-password").fill("not-the-password");
    await dialog.getByRole("button", { name: /^confirm$/i }).click();
    await expect(dialog.getByRole("alert").or(dialog.locator("#stepup-error"))).toBeVisible();
    await expect(dialog).toBeVisible();
    expect(portal.calls()).toBe(2);

    await dialog.locator("#stepup-password").fill(PASSWORD);
    const signIn = page.waitForResponse((response) => /\/auth\/login$/.test(response.url()));
    await dialog.getByRole("button", { name: /^confirm$/i }).click();
    expect((await signIn).status(), "a real sign-in restamps the clock").toBe(200);
    await expect(dialog).toBeHidden();
    await page.waitForURL(/\/portal-opened$/);
    expect(portal.calls(), "the action ran once more, by itself").toBe(3);
  });
});

test.describe("A form that asks for the password itself", () => {
  test.use({ user: "C.owner" });

  test("a wrong password when proposing an ownership transfer is the form's error, not a second dialog", async ({ page, problems }) => {
    problems.allowHttp(/\/ownership-transfers$/, [401], "a wrong password is refused");
    await page.goto(org("C", "members"));
    await page.getByRole("button", { name: /transfer ownership/i }).click();
    const target = page.locator("#transfer-target");
    const firstCandidate = await target.locator("option").nth(1).getAttribute("value");
    await target.selectOption(firstCandidate!);
    await page.locator("#transfer-password").fill("not-the-password");
    const refused = page.waitForResponse((response) => /\/ownership-transfers$/.test(response.url()));
    await page.getByRole("button", { name: /propose transfer/i }).click();
    expect((await refused).status()).toBe(401);
    await expect(page.getByRole("alert").filter({ hasText: /current password is incorrect/i })).toBeVisible();
    await page.waitForTimeout(500);
    await expect(page.getByRole("dialog", { name: /confirm it.s you/i })).toHaveCount(0);
  });
});
