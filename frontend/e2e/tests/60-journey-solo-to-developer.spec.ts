/**
 * JOURNEY 1 — a solo user from sign-up to the developer API (campaign session 4, §D).
 *
 * Signs up and verifies through the real forms and email, creates an organization (the one-week
 * trial starts), uploads a document, hits a Free limit and follows the upgrade path to the plans,
 * upgrades to Developer, creates an API key, uploads through the API with it, and Servabo sends
 * the webhook for that document.
 *
 * Stood in: Stripe's hosted checkout page (the request Servabo makes is checked, the payment is
 * then confirmed by `backend/scripts/e2e_stand_in.py` through Servabo's own reconcile code), and
 * the customer's webhook receiver: the sandbox cannot host a public HTTPS endpoint, so the journey
 * proves Servabo sent the signed delivery for the uploaded document (the receiving end, with its
 * signature check and retries, is proven live in `backend/tests/engines/test_webhooks_live.py`).
 */
import fs from "node:fs";
import path from "node:path";

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api, apiLogin } from "../support/api";
import { API_BASE, PASSWORD, runId } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";
import { documents, invoicePdf, standIn, uploadInBrowser, type Customer } from "../support/journey";

test.describe("Journey: a solo user upgrades to Developer and uses the API", () => {
  test.setTimeout(360_000);

  test("sign up → trial → Free limit → Developer → API key → API upload → webhook", async ({ page, problems }) => {
    const email = `solo-${runId()}@e2e.example.com`;

    // Sign up and verify through the real forms and email.
    const since = Date.now() - 1_000;
    await page.goto("/register");
    await page.locator("#email").fill(email);
    await page.locator("#password").fill(PASSWORD);
    await page.locator("#confirmPassword").fill(PASSWORD);
    await page.locator("form").getByRole("button", { name: /create|sign up|register/i }).click();
    await expect(page.locator("body")).toContainText(/check your (email|inbox)|verification|sent/i);
    await page.goto(linkFrom(await waitForMail(email, /verify-email/, since), /\/verify-email/));
    await expect(page.locator("body")).toContainText(/verified|confirmed|thank/i, { timeout: 15_000 });
    await page.goto("/login");
    await page.getByRole("textbox", { name: "Email" }).fill(email);
    await page.getByRole("button", { name: "Continue" }).click();
    await page.locator("#password").fill(PASSWORD);
    await page.locator("#password").press("Enter");
    await expect(page).toHaveURL(/\/onboarding|\/organizations\/new|\/workspaces/, { timeout: 20_000 });

    // The first organization: the trial starts.
    const orgName = `Solo Labs ${runId()}`;
    await page.getByRole("textbox").first().fill(orgName);
    await page.getByRole("button", { name: /create|continue|get started/i }).first().click();
    await expect(page.locator("body")).toContainText(orgName, { timeout: 20_000 });
    const session = await apiLogin(email);
    const context = await api<{ organizations: Array<{ organization_id: string; organization_slug: string;
      workspaces: Array<{ id: string; slug: string }> }> }>(session, "GET", "/me/context");
    const found = context.body.organizations[0];
    expect(found).toBeTruthy();
    const customer: Customer = {
      email, session, orgSlug: found!.organization_slug, orgId: found!.organization_id,
      wsSlug: found!.workspaces[0]!.slug, wsId: found!.workspaces[0]!.id,
      ws: (sub = "") => `/${found!.organization_slug}/${found!.workspaces[0]!.slug}${sub ? `/${sub}` : ""}`,
      org: (sub = "") => `/organizations/${found!.organization_slug}${sub ? `/${sub}` : ""}`,
    };
    await uploadInBrowser(page, customer, invoicePdf("solo-first"));
    await expect.poll(async () => (await documents(customer)).length, { timeout: 60_000 }).toBe(1);

    // A Free limit (one workspace), with the way on.
    problems.allowHttp(/\/organizations\/[^/]+\/workspaces$/, [402], "Free includes one workspace");
    await page.goto(customer.org("workspaces/new"));
    await page.getByLabel("Workspace name").fill("Second desk");
    await page.getByRole("button", { name: "Create workspace" }).click();
    const limit = page.getByRole("alert").filter({ hasText: "includes 1 workspace" });
    await expect(limit).toBeVisible({ timeout: 15_000 });
    await limit.getByTestId("view-plans").click();
    await expect(page).toHaveURL(new RegExp(`${customer.org("billing")}$`));

    // Upgrade to Developer: Servabo opens the gateway's checkout for one seat of Developer.
    let checkout: Record<string, unknown> | null = null;
    await page.route(/\/billing\/plans/, async (route) => {
      const response = await route.fetch();
      await route.fulfill({ response, json: { ...(await response.json()), checkout_available: true } });
    });
    await page.route(/\/billing\/checkout-session/, async (route) => {
      checkout = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({ json: { url: new URL("/__checkout__", page.url()).toString(), kind: "checkout" } });
    });
    await page.route(/__checkout__/, (route) => route.fulfill({ contentType: "text/html", body: "<h1>Checkout</h1>" }));
    await page.getByTestId("plan-cta-developer").click();
    await page.getByRole("button", { name: "Continue to payment" }).click();
    await expect(page).toHaveURL(/__checkout__/);
    expect(checkout).toMatchObject({ quota_tier_key: "developer", seats: 1 });
    standIn("subscribe", customer.orgSlug, "--plan", "developer", "--seats", "1");  // paid at the gateway
    await page.goto(customer.org("billing"));
    await expect(page.locator("main")).toContainText(/Developer/);

    // A webhook endpoint for finished documents, and an API key, both in the browser.
    await page.goto(customer.org("webhooks"));
    await page.getByRole("button", { name: "New endpoint" }).click();
    await page.locator("#hook-url").fill("https://hooks.example.com/servabo");
    await page.getByRole("checkbox", { name: "document.completed" }).check();
    await page.getByRole("button", { name: "Create endpoint" }).click();
    await expect(page.getByRole("region", { name: "Webhook signing secret" })).toBeVisible();

    await page.goto(customer.org("api-keys"));
    await page.getByRole("button", { name: "New key" }).click();
    await page.getByLabel("Name").fill("Invoice importer");
    for (const scope of ["workspaces:read", "work_items:read", "work_items:write"]) {
      await page.getByRole("checkbox", { name: scope }).check();
    }
    await page.getByRole("button", { name: "Create key" }).click();
    const key = (await page.getByRole("region", { name: "New API key" }).locator("code").innerText()).trim();

    // An upload through the API with that key; the document is processed and the webhook is sent.
    const file = invoicePdf("solo-api");
    const form = new FormData();
    form.append("file", new Blob([fs.readFileSync(file)], { type: "application/pdf" }), path.basename(file));
    const uploaded = await fetch(`${API_BASE}/workspaces/${customer.wsId}/work-items`, {
      method: "POST", headers: { Authorization: `Bearer ${key}` }, body: form,
    });
    expect(uploaded.status, await uploaded.text()).toBe(201);
    await expect.poll(async () => (await documents(customer)).filter((d) => d.status === "COMPLETED").length,
      { timeout: 120_000 }).toBe(2);

    // The worker sends the webhook a moment after the document completes, and the Deliveries
    // panel lists what exists when it opens: wait for the server to have recorded the delivery.
    const hooks = `/organizations/${customer.orgId}/webhooks`;
    await expect.poll(async () => {
      const endpoints = await api<unknown>(customer.session, "GET", `${hooks}/endpoints`);
      const list = (Array.isArray(endpoints.body) ? endpoints.body : (endpoints.body as { items?: unknown[] })?.items ?? []) as Array<{ id: string }>;
      if (!list[0]) return 0;
      const sent = await api<unknown>(customer.session, "GET", `${hooks}/endpoints/${list[0].id}/deliveries`);
      const rows = (Array.isArray(sent.body) ? sent.body : (sent.body as { items?: unknown[] })?.items ?? []) as unknown[];
      return rows.length;
    }, { timeout: 120_000, intervals: [2_000, 3_000] }).toBeGreaterThan(0);

    await page.goto(customer.org("webhooks"));
    await page.getByRole("button", { name: "Deliveries" }).first().click();
    await expect(page.getByRole("button", { name: "Attempt history for document.completed" }).first())
      .toBeVisible({ timeout: 60_000 });
    await expectHealthyPage(page);
  });
});
