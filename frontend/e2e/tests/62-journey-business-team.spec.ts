/**
 * JOURNEY 3 — a Business team (campaign session 4, §D).
 *
 * The owner buys seats (the price is shown and confirmed first), then invites an admin, a
 * contributor and a viewer from the members page; each accepts by email. Each does exactly what
 * their role allows and is stopped politely (and by the server) where it does not. One member is
 * removed and loses access at once.
 *
 * Stood in: the payment gateway's side of the seat change (`backend/scripts/e2e_stand_in.py` sets
 * the quantity as Stripe would and FlowPilot records it through its reconcile code); the page then
 * shows FlowPilot's own answer.
 */
import fs from "node:fs";
import path from "node:path";

import type { Page } from "@playwright/test";

import { signUpVerified, tokenOf } from "../support/accounts";
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api, apiLogin, type ApiSession } from "../support/api";
import { API_BASE, runId } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";
import { invoicePdf, newCustomer, signInAs, standIn, uploadInBrowser, type Customer } from "../support/journey";

async function inviteFromMembersPage(page: Page, customer: Customer, label: string, organizationRole: string,
  workspaceRole: string): Promise<{ email: string; session: ApiSession }> {
  const email = `${label}-${runId()}@e2e.example.com`;
  const person = await signUpVerified(email);
  const since = Date.now() - 1_000;
  await page.goto(customer.org("members"));
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("combobox", { name: "Organization role" }).selectOption({ label: organizationRole });
  await page.getByRole("checkbox", { name: "Main" }).check();
  await page.getByRole("combobox", { name: "Role in Main" }).selectOption({ label: workspaceRole });
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByRole("list", { name: "Pending invitations" })).toContainText(email);
  const mail = await waitForMail(email, /invitations\/accept/, since);
  const accepted = await api(person, "POST", "/invitations/accept", { token: tokenOf(linkFrom(mail, /invitations\/accept/)) });
  expect(accepted.status, accepted.text).toBe(200);
  return { email, session: await apiLogin(email) };
}

async function uploadAs(session: ApiSession, workspaceId: string, label: string): Promise<number> {
  const file = invoicePdf(label);
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(file)], { type: "application/pdf" }), path.basename(file));
  const response = await fetch(`${API_BASE}/workspaces/${workspaceId}/work-items`, {
    method: "POST", headers: { Authorization: `Bearer ${session.accessToken}` }, body: form,
  });
  return response.status;
}

test.describe("Journey: a Business team", () => {
  test.setTimeout(420_000);

  test("seats bought, three roles invited, each allowed and stopped as its role says, one removed", async ({ page, problems }) => {
    const customer = await newCustomer("team-journey", "Team Journey Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "business", "--seats", "1");
    await signInAs(page, customer.email);

    // The owner buys three more seats: the price first, then the change at the gateway.
    const bought: { body?: { seats?: number; confirmed_unit_price_micros?: number } } = {};
    await page.route(/\/billing\/seats$/, async (route) => {
      if (route.request().method() !== "PUT") return route.continue();
      bought.body = route.request().postDataJSON() as { seats?: number; confirmed_unit_price_micros?: number };
      standIn("seats", customer.orgSlug, "--seats", String(bought.body.seats));
      const state = await route.fetch({ url: route.request().url().replace(/\/seats$/, "/subscription"), method: "GET" });
      await route.fulfill({ response: state });
    });
    await page.goto(customer.org("billing"));
    const seats = page.getByRole("region", { name: "Seats" });
    for (let n = 0; n < 3; n += 1) {
      await seats.getByRole("button", { name: "One seat more" }).click();
    }
    await seats.getByRole("button", { name: "Review change" }).click();
    const confirm = seats.getByRole("dialog", { name: "Confirm seat change" });
    await expect(confirm).toContainText("$299.00 per seat per month", { timeout: 15_000 });
    await confirm.getByRole("button", { name: "Buy 3 seats" }).click();
    await expect.poll(() => bought.body?.seats).toBe(4);
    expect(bought.body?.confirmed_unit_price_micros).toBe(299_000_000);
    await expect(seats).toContainText("4", { timeout: 15_000 });

    // Three people invited from the members page; each accepts by email.
    const admin = await inviteFromMembersPage(page, customer, "team-admin", "Admin", "Admin");
    const contributor = await inviteFromMembersPage(page, customer, "team-contributor", "Member", "Contributor");
    const viewer = await inviteFromMembersPage(page, customer, "team-viewer", "Member", "Viewer");

    // The admin manages people but not billing.
    await signInAs(page, admin.email);
    await page.goto(customer.org("members"));
    await expect(page.getByRole("button", { name: "Send invitation" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Billing", exact: true })).toHaveCount(0);
    // An admin may buy seats for the people they invite, but the plan, the card and cancelling are the
    // owner's (TENANCY-AND-PLANS §3.1): the billing portal is refused to them.
    expect((await api(admin.session, "POST", `/organizations/${customer.orgId}/billing/portal-session`,
      { return_url: "http://localhost:3000/" })).status).toBe(403);

    // The contributor uploads, and may not invite anyone.
    await signInAs(page, contributor.email);
    await uploadInBrowser(page, customer, invoicePdf("team-contributor"));
    await page.goto(customer.org("general"));
    await expect(page.getByRole("link", { name: "Members", exact: true })).toHaveCount(0);
    expect((await api(contributor.session, "POST", `/organizations/${customer.orgId}/invitations`,
      { email: `nobody-${runId()}@e2e.example.com`, organization_role: "MEMBER", grants: [] })).status).toBe(403);

    // The viewer reads, and is told uploads are not for their role; the server agrees.
    await signInAs(page, viewer.email);
    await page.goto(customer.ws("work-items"));
    await expect(page.getByRole("table").getByText(/team-contributor/)).toBeVisible({ timeout: 30_000 });
    expect(await uploadAs(viewer.session, customer.wsId, "team-viewer")).toBe(403);
    await expectHealthyPage(page);

    // The owner removes the contributor, who loses access at once.
    await signInAs(page, customer.email);
    await page.goto(customer.org("members"));
    problems.allowHttp(/\/work-items$/, [403, 404], "the removed contributor's upload is refused");
    await page.getByRole("button", { name: `Remove ${contributor.email}` }).click();
    await page.getByRole("button", { name: "Confirm removal" }).click();
    // The list then shows them as removed, or no longer lists them.
    await expect.poll(async () => {
      const row = page.getByText(contributor.email).locator("xpath=ancestor::li[1]");
      return (await row.count()) === 0 || /Removed/.test(await row.innerText());
    }, { timeout: 15_000 }).toBe(true);
    const after = await uploadAs(contributor.session, customer.wsId, "team-removed");
    expect([403, 404]).toContain(after);
    await expectHealthyPage(page);
  });
});
