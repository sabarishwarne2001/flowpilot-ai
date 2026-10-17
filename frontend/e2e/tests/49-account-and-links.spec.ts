/**
 * Campaign session 3 (coverage ledger) — the account pages and the links that arrive by email.
 *
 * The server half of each was proven (password and email change, single sign-on, the public
 * document request); the browser half was not. Each test here goes through the real page:
 * a fresh account changes its password and its email address from the profile, with the
 * confirmation link read from the local mail sink; a browser holding only the cookie a single
 * sign-on leaves completes the sign-in; a supplier with no account answers a document request;
 * and the "unavailable" page says what happened and where to go.
 */
import type { Browser, BrowserContext } from "@playwright/test";

import { test, expect } from "../support/fixtures";
import { api, apiLogin, loginAs, type ApiSession } from "../support/api";
import { openAs, signUpVerified } from "../support/accounts";
import { BROWSER_API_ORIGIN, PASSWORD, TENANTS, runId } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";
import { buildPdf } from "../support/sample-docs";

const NEW_PASSWORD = "E2e-Changed-Pass-2026!";

interface MeContext {
  organizations: Array<{ organization_slug: string; workspaces: Array<{ id: string; slug: string }> }>;
}

/** A new person with an organization of their own; returns the path to their settings. */
async function freshAccount(prefix: string): Promise<{ email: string; session: ApiSession; settings: string }> {
  const email = `${prefix}-${runId()}@e2e.example.com`;
  const session = await signUpVerified(email);
  const created = await api(session, "POST", "/organizations", { organization_name: `${prefix} ${runId()}` });
  expect(created.status, created.text).toBe(201);
  const context = await api<MeContext>(session, "GET", "/me/context");
  const organization = context.body.organizations[0]!;
  return { email, session, settings: `/${organization.organization_slug}/${organization.workspaces[0]!.slug}/settings` };
}

async function cannotSignIn(email: string, password: string): Promise<void> {
  await expect(apiLogin(email, password)).rejects.toThrow(/HTTP 4\d\d/);
}

test.describe("Account changes from the profile", () => {
  test.setTimeout(150_000);

  test("a new password signs in, the old one no longer does", async ({ browser }) => {
    const { email, session, settings } = await freshAccount("pwchange");
    const { context, page } = await openAs(browser, session);
    try {
      await page.goto(settings);
      await page.getByRole("button", { name: "Change password" }).first().click();
      await page.locator("#password-current").fill(PASSWORD);
      await page.locator("#password-new").fill(NEW_PASSWORD);
      await page.locator("#password-confirm").fill(NEW_PASSWORD);
      await page.getByRole("button", { name: "Change password" }).last().click();
      await expect(page.locator("body")).toContainText(/Password changed\. Every other session was signed out/, {
        timeout: 15_000,
      });
      await apiLogin(email, NEW_PASSWORD);
      await cannotSignIn(email, PASSWORD);
    } finally {
      await context.close();
    }
  });

  test("an email change finishes from the link in the mail; the new address signs in", async ({ browser }) => {
    const { email, session, settings } = await freshAccount("emchange");
    const newEmail = `moved-${runId()}@e2e.example.com`;
    const { context, page } = await openAs(browser, session);
    try {
      await page.goto(settings);
      await page.getByRole("button", { name: /Change email address/ }).click();
      await page.locator("#new-email").fill(newEmail);
      await page.locator("#email-password").fill(PASSWORD);
      const since = Date.now() - 1_000;
      await page.getByRole("button", { name: "Send confirmation link" }).click();
      await expect(page.locator("body")).toContainText(`Waiting for you to confirm ${newEmail}`, { timeout: 15_000 });
      await apiLogin(email); // nothing changes until the link is opened

      const link = linkFrom(await waitForMail(newEmail, /confirm-email-change/, since), /\/confirm-email-change/);
      await page.goto(link);
      await expect(page.getByRole("heading", { name: "Email address updated" })).toBeVisible({ timeout: 15_000 });
      await expect(page.locator("body")).toContainText(newEmail);
      await apiLogin(newEmail);
      await cannotSignIn(email, PASSWORD);

      // Confirming signed this browser out too, and the link works once.
      await page.goto("/workspaces");
      await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
      await page.goto(link);
      await expect(page.locator("body")).toContainText("That link didn't work", { timeout: 15_000 });
    } finally {
      await context.close();
    }
  });
});

async function cookieOnly(browser: Browser, session: ApiSession | null): Promise<BrowserContext> {
  const context = await browser.newContext();
  if (session) {
    await context.addCookies([{
      name: "servabo_refresh", value: session.refreshToken, domain: new URL(BROWSER_API_ORIGIN).hostname,
      path: "/api/v1/auth", httpOnly: true, secure: false, sameSite: "Lax",
    }]);
  }
  return context;
}

test.describe("Single sign-on completion page", () => {
  test("a browser holding only the sign-on cookie arrives signed in, at a safe destination", async ({ browser }) => {
    const context = await cookieOnly(browser, await loginAs("C.owner"));
    try {
      const page = await context.newPage();
      await page.goto(`/auth/sso/complete?next=${encodeURIComponent(`/${TENANTS.C.org}/${TENANTS.C.ws}`)}`);
      await expect(page).toHaveURL(new RegExp(`/${TENANTS.C.org}/${TENANTS.C.ws}`), { timeout: 20_000 });
      await expect(page.locator("main").first()).toBeVisible();
    } finally {
      await context.close();
    }

    const elsewhere = await cookieOnly(browser, await loginAs("C.owner"));
    try {
      const page = await elsewhere.newPage();
      await page.goto(`/auth/sso/complete?next=${encodeURIComponent("https://evil.example/steal")}`);
      await expect(page).toHaveURL(/\/workspaces|\/caretakers-global/, { timeout: 20_000 });
      expect(new URL(page.url()).hostname).toBe("localhost");
    } finally {
      await elsewhere.close();
    }
  });

  test("without the cookie it stops and says why, with a way back to sign in", async ({ browser, problems }) => {
    problems.allowHttp(/\/auth\/refresh/, [401], "no session cookie: the exchange is refused, as it should be");
    const context = await cookieOnly(browser, null);
    try {
      const page = await context.newPage();
      await page.goto("/auth/sso/complete");
      await expect(page.getByRole("heading", { name: "We couldn't finish signing you in" })).toBeVisible({ timeout: 20_000 });
      await page.getByRole("link", { name: "Back to sign in" }).click();
      await expect(page).toHaveURL(/\/login/);
    } finally {
      await context.close();
    }
  });
});

test.describe("A document request answered without an account", () => {
  test("the supplier sees what is asked, uploads it once, and the link closes", async ({ browser }) => {
    const owner = await loginAs("C.owner");
    const context = await api<MeContext>(owner, "GET", "/me/context");
    const workspace = context.body.organizations
      .flatMap((organization) => organization.workspaces)
      .find((candidate) => candidate.slug === TENANTS.C.ws)!;
    const base = `/workspaces/${workspace.id}`;
    const key = `intake-${runId()}`.toLowerCase().replace(/[^a-z0-9-]/g, "-");
    const template = await api<{ id: string }>(owner, "POST", `${base}/case-templates`, {
      key, name: "Supplier intake", required_documents: [{ doc_type: "invoice", label: "Invoice" }], rules: [],
    });
    expect(template.status, template.text).toBe(201);
    expect((await api(owner, "POST", `${base}/case-templates/${template.body.id}/publish`)).status).toBe(200);
    const title = `Supplier onboarding ${runId()}`;
    const kase = await api<{ case: { id: string } }>(owner, "POST", `${base}/cases`, { template_id: template.body.id, title });
    expect(kase.status, kase.text).toBe(201);
    const request = await api<{ token: string }>(owner, "POST", `${base}/cases/${kase.body.case.id}/requests`, {
      document_type: "invoice", recipient_label: "Supplier",
    });
    expect(request.status, request.text).toBe(201);

    const visitor = await browser.newContext();
    try {
      const page = await visitor.newPage();
      await page.goto(`/request/${request.body.token}`);
      await expect(page.getByRole("heading", { name: "Upload a requested document" })).toBeVisible();
      await expect(page.locator("main")).toContainText(`${title} needs a invoice`);
      await page.getByLabel("Document").setInputFiles({
        name: "invoice.pdf", mimeType: "application/pdf", buffer: buildPdf([["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice No: REQ-1"]]),
      });
      await page.getByRole("button", { name: "Upload" }).click();
      await expect(page.locator("main")).toContainText("Received. Thank you — this link is now closed.", { timeout: 20_000 });

      const used = await api(null, "GET", `/public/document-requests/${request.body.token}`);
      expect(used.status).toBeGreaterThanOrEqual(400);
    } finally {
      await visitor.close();
    }
  });
});

test.describe("The unavailable page", () => {
  test.use({ user: "C.owner" });

  test("says what is unavailable and leads back to the workspaces", async ({ page }) => {
    await page.goto("/no-access?reason=workspace_archived");
    await expect(page.getByRole("heading", { name: "This workspace is unavailable" })).toBeVisible();
    await expect(page.locator("body")).toContainText("its documents are retained");
    await page.goto("/no-access?reason=organization_suspended");
    await expect(page.getByRole("heading", { name: "This organization is unavailable" })).toBeVisible();
    await page.getByRole("link", { name: "Choose another workspace" }).click();
    await expect(page).toHaveURL(/\/workspaces/);
  });
});
