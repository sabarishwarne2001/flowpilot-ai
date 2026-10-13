/**
 * JOURNEY 4 — an Enterprise customer (campaign session 4, §D).
 *
 * Identity: the owner claims the company's email domain and proves it with a DNS record, connects
 * the company's SAML identity provider, and an employee signs in through it for the first time
 * (created on the way in). The provider then provisions a second person over SCIM and later
 * switches them off. The owner locks outbound connections down to an allow list, and a webhook to
 * a host that is not on it is refused. The audit export records all of it.
 *
 * Daily work: two documents are compared by the corroborator, and two reviewers see each other in
 * live collaborative review of one queue.
 *
 * Stood in: the customer's DNS provider (`backend/scripts/e2e_dns.py` answers FlowPilot's real DNS
 * lookup with the published record) and the SAML identity provider (`e2e_stand_in.py saml-idp` and
 * `saml-response`: a signing certificate, then a signed assertion answering FlowPilot's own
 * AuthnRequest, posted to FlowPilot's ACS by the browser as a provider's page does).
 */
import fs from "node:fs";

import type { Browser, Page } from "@playwright/test";

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api } from "../support/api";
import { API_ORIGIN, WEB_ORIGIN, runId } from "../support/env";
import { addMember, newCustomer, signInAs, standIn, uploadViaApi, type Customer } from "../support/journey";

async function ssoSignIn(browser: Browser, domain: string, email: string): Promise<Page> {
  const context = await browser.newContext({ baseURL: WEB_ORIGIN });
  const page = await context.newPage();
  // FlowPilot sends the browser to the provider with a redirect. Playwright does not intercept a
  // page reached by a redirect, so the redirect is read here (it must name the configured provider)
  // and followed as an ordinary navigation, which the provider's stand-in below then answers.
  await page.route(/\/api\/v1\/sso\/start/, async (route) => {
    const response = await route.fetch({ maxRedirects: 0 });
    const location = response.headers()["location"] ?? "";
    expect(response.status(), await response.text()).toBe(302);
    expect(location.startsWith(`http://idp.${domain}/sso?SAMLRequest=`), location).toBe(true);
    await route.fulfill({ contentType: "text/html",
      body: `<!doctype html><meta http-equiv="refresh" content="0;url=${location.replace(/"/g, "&quot;")}">` });
  });
  // The provider's sign-in page: the person signs in there, and it posts the signed answer back.
  await page.route(new RegExp(`^http://idp\\.${domain.replace(/\./g, "\\.")}/sso`), async (route) => {
    const url = new URL(route.request().url());
    const answer = standIn("saml-response", null, "--domain", domain, "--email", email,
      "--saml-request", url.searchParams.get("SAMLRequest") ?? "") as { acs_url: string; saml_response: string };
    const relay = url.searchParams.get("RelayState");
    await route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><title>Identity provider</title><form method="post" action="${answer.acs_url}">`
        + `<input type="hidden" name="SAMLResponse" value="${answer.saml_response}">`
        + (relay ? `<input type="hidden" name="RelayState" value="${relay.replace(/"/g, "&quot;")}">` : "")
        + "</form><script>document.forms[0].submit()</script>",
    });
  });
  await page.goto("/login");
  await page.getByRole("textbox", { name: "Email" }).fill(email);
  await page.getByRole("button", { name: "Continue" }).click();
  return page;
}

async function scim(token: string, method: string, path: string, body?: unknown): Promise<{ status: number; json: Record<string, unknown> }> {
  const response = await fetch(`${API_ORIGIN}/scim/v2${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/scim+json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, json: text ? (JSON.parse(text) as Record<string, unknown>) : {} };
}

async function enterprise(label: string, name: string): Promise<Customer> {
  const customer = await newCustomer(label, name);
  standIn("subscribe", customer.orgSlug, "--plan", "enterprise", "--seats", "10");
  return customer;
}

test.describe("Journey: an Enterprise customer", () => {
  test.setTimeout(480_000);

  test("domain proven by DNS, SAML sign-in, SCIM provisioning, egress lockdown, all in the audit export",
    async ({ page, browser, problems }) => {
      const customer = await enterprise("enterprise-journey", "Enterprise Journey Corp");
      const domain = `ent-${runId()}.example.com`;
      await signInAs(page, customer.email);

      // The domain: claimed, checked before the record exists (not yet), published, checked again.
      problems.allowHttp(/\/identity\/domains\/[^/]+\/verify$/, [409], "the TXT record is checked before it is published");
      await page.goto(customer.org("identity?tab=domains"));
      await page.getByLabel("Domain to claim").fill(domain);
      await page.getByRole("button", { name: "Claim domain" }).click();
      const claimed = page.getByRole("listitem").filter({ hasText: domain }).first();  // the domain row, not the registrar tips in it
      await expect(claimed).toContainText("Awaiting DNS", { timeout: 15_000 });
      const record = (await claimed.locator("dd .font-mono").last().innerText()).trim();
      expect(record).toMatch(/^flowpilot-site-verification=/);
      await claimed.getByRole("button", { name: "Check DNS" }).click();
      await expect(page.getByText(/not found|could not|TXT record/i).first()).toBeVisible({ timeout: 15_000 });
      standIn("dns-publish", null, "--name", domain, "--value", record);  // at the customer's DNS provider
      await claimed.getByRole("button", { name: "Check DNS" }).click();
      await expect(page.getByText(`${domain} is verified.`)).toBeVisible({ timeout: 15_000 });
      await expect(claimed).toContainText("Verified");
      await claimed.getByRole("button", { name: "Bind to SSO" }).click();
      await expect(page.getByText(`${domain} now signs people in with single sign-on.`)).toBeVisible({ timeout: 15_000 });

      // The identity provider: a SAML connection that lets the company's people in, its certificate, active.
      const idp = standIn("saml-idp", null, "--domain", domain) as { entity_id: string; sso_url: string; certificate: string };
      await page.goto(customer.org("identity?tab=sso"));
      await page.getByRole("button", { name: "Add connection" }).click();
      await page.getByRole("group", { name: "Protocol" }).getByRole("button", { name: "SAML 2.0" }).click();
      await page.getByLabel("Name").fill("Company sign-in");
      await page.getByLabel("Verified domain").selectOption({ label: domain });
      await page.getByLabel("Entity ID").fill(idp.entity_id);
      await page.getByLabel("SSO URL").fill(idp.sso_url);
      await page.getByRole("button", { name: "open", exact: true }).click();
      await page.getByRole("button", { name: "Create", exact: true }).click();
      const connection = page.getByRole("button", { name: /Company sign-in/ });
      await expect(connection).toBeVisible({ timeout: 15_000 });
      if ((await connection.getAttribute("aria-expanded")) !== "true") await connection.click();
      await page.getByRole("button", { name: "Add", exact: true }).click();
      await page.getByLabel("Certificate PEM").fill(idp.certificate);
      await page.getByRole("button", { name: "Add certificate" }).click();
      await page.getByRole("button", { name: "Activate connection" }).click();
      await expect(page.getByText(/The connection is active/)).toBeVisible({ timeout: 15_000 });

      // An employee signs in through it for the first time: created on the way in, in the organization.
      const jo = `jo.rivera@${domain}`;
      const employee = await ssoSignIn(browser, domain, jo);
      await expect(employee.locator("body")).toContainText("Enterprise Journey Corp", { timeout: 30_000 });
      await expectHealthyPage(employee);
      await employee.context().close();

      // SCIM: the provider gets a token, provisions a second person, and later switches them off.
      await page.goto(customer.org("identity?tab=scim"));
      await page.getByLabel("Identity provider for new token").selectOption({ label: "Company sign-in" });
      await page.getByRole("button", { name: "Issue token" }).click();
      const secret = page.getByRole("dialog");
      const token = (await secret.locator("code").innerText()).trim();
      await secret.getByRole("checkbox", { name: "I have stored this token somewhere safe." }).check();
      await secret.getByRole("button", { name: "Done" }).click();
      const sam = `sam.okafor@${domain}`;
      const created = await scim(token, "POST", "/Users", {
        schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"], userName: sam, active: true,
        name: { givenName: "Sam", familyName: "Okafor" }, emails: [{ value: sam, primary: true }],
      });
      expect(created.status, JSON.stringify(created.json)).toBe(201);
      await page.goto(customer.org("members"));
      await expect(page.locator("main")).toContainText(sam, { timeout: 15_000 });
      const off = await scim(token, "PATCH", `/Users/${String(created.json.id)}`, {
        schemas: ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        Operations: [{ op: "replace", path: "active", value: false }],
      });
      expect(off.status, JSON.stringify(off.json)).toBe(200);
      await page.reload();
      await expect.poll(async () => {
        const row = page.getByText(sam).locator("xpath=ancestor::li[1]");
        return (await row.count()) === 0 || /Removed|Deactivated|Disabled/i.test(await row.innerText());
      }, { timeout: 15_000 }).toBe(true);

      // Egress lockdown: only the allow list may be reached; a webhook to anywhere else is refused.
      await page.goto(customer.org("egress"));
      await page.getByRole("textbox", { name: "Host, *.domain, IP or CIDR" }).fill(`hooks.${domain}`);
      await page.getByRole("textbox", { name: "Port" }).fill("443");
      await page.getByRole("button", { name: "Add rule" }).click();
      await expect(page.locator("main")).toContainText(`hooks.${domain}`, { timeout: 10_000 });
      await page.getByRole("button", { name: "Switch lockdown on" }).click();
      const confirm = page.getByRole("alertdialog").or(page.getByRole("dialog"));
      if (await confirm.isVisible()) await confirm.getByRole("button").last().click();
      await expect(page.locator("main")).toContainText("Lockdown is on", { timeout: 10_000 });
      await page.getByRole("textbox", { name: "URL or host" }).fill("https://hooks.elsewhere.example.net/flowpilot");
      await page.getByRole("button", { name: "Test", exact: true }).click();
      await expect(page.getByTestId("egress-decision")).toContainText(/Refused/);
      await page.getByRole("textbox", { name: "URL or host" }).fill(`https://hooks.${domain}/flowpilot`);
      await page.getByRole("button", { name: "Test", exact: true }).click();
      await expect(page.getByTestId("egress-decision")).not.toContainText(/Refused/);

      problems.allowHttp(/\/webhooks\//, [400, 422, 502], "the webhook host is outside the allow list");
      await page.goto(customer.org("webhooks"));
      await page.getByRole("button", { name: "New endpoint" }).click();
      const outside = "https://hooks.elsewhere.example.net/flowpilot";
      await page.getByRole("textbox", { name: "Endpoint URL" }).fill(outside);
      await page.getByRole("checkbox", { name: "work_item.created" }).check();
      await page.getByRole("button", { name: /create|save|add endpoint/i }).last().click();
      await page.getByRole("button", { name: "I've saved it" }).click();
      const endpoint = page.getByRole("listitem").filter({ has: page.getByRole("button", { name: `Delete ${outside}` }) });
      await endpoint.getByRole("button", { name: "Deliveries" }).click();
      await endpoint.getByRole("button", { name: "Send test event" }).click();
      await expect(page.getByText(/Test event queued/)).toBeVisible({ timeout: 15_000 });
      await expect.poll(async () => {
        await page.goto(customer.org("egress"));
        return page.getByRole("region", { name: "Refused connections" }).or(page.locator("section", { hasText: "Refused connections" }))
          .first().innerText();
      }, { timeout: 90_000, intervals: [3_000] }).toContain("hooks.elsewhere.example.net");

      // The audit export: every step above, in the file the owner downloads.
      await page.goto(customer.org("audit"));
      const download = page.waitForEvent("download");
      await page.getByRole("button", { name: "CSV" }).click();
      const file = await (await download).path();
      const csv = fs.readFileSync(file, "utf8");
      for (const evidence of ["VERIFIED_DOMAIN", "ENTERPRISE_IDP_CONFIG", "SAML2", "SCIM", "egress"]) {
        expect(csv, `the audit export records ${evidence}`).toContain(evidence);
      }
      await expectHealthyPage(page);
    });

  test("two documents corroborated, and two reviewers see each other in live review", async ({ page, browser }) => {
    const customer = await enterprise("enterprise-review", "Enterprise Review Corp");
    // A second reviewer, an admin of the organization.
    const reviewer = await addMember(customer, "enterprise-reviewer", "ADMIN", "ADMIN");
    const first = await uploadViaApi(customer, "enterprise-po");
    const second = await uploadViaApi(customer, "enterprise-invoice");
    await expect.poll(async () => {
      const items = await api<{ items?: Array<{ status: string }> } | Array<{ status: string }>>(customer.session, "GET",
        `/workspaces/${customer.wsId}/work-items`);
      const list = Array.isArray(items.body) ? items.body : (items.body.items ?? []);
      return list.filter((item) => item.status === "COMPLETED").length;
    }, { timeout: 180_000, intervals: [3_000] }).toBe(2);

    // The corroborator compares the two documents.
    await signInAs(page, customer.email);
    await page.goto(customer.ws("corroboration"));
    await page.getByRole("button", { name: "New comparison" }).click();
    await page.getByRole("checkbox", { name: /enterprise-po/ }).check();
    await page.getByRole("checkbox", { name: /enterprise-invoice/ }).check();
    await page.getByRole("button", { name: "Compare" }).click();
    await expect(page.locator("main")).toContainText(/enterprise-po/, { timeout: 120_000 });
    await expect(page.locator("main")).not.toContainText(/failed/i, { timeout: 120_000 });
    expect(first).not.toEqual(second);

    // Two reviewers on the same queue see each other.
    await page.goto(customer.ws("verification"));
    const other = await browser.newContext();
    const colleague = await other.newPage();
    await signInAs(colleague, reviewer.email);
    await colleague.goto(customer.ws("verification"));
    await expect(page.getByText(/Live · 1 other here/)).toBeVisible({ timeout: 30_000 });
    await expect(colleague.getByText(/Live · 1 other here/)).toBeVisible({ timeout: 30_000 });
    await other.close();
    await expectHealthyPage(page);
  });
});
