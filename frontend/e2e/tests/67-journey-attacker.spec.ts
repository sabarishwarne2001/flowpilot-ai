/**
 * JOURNEY 8 — an attacker tries throughout (campaign session 4, §D; SECURITY-REVIEW.md).
 *
 * Three personas against a real customer with a real document:
 * - an OUTSIDER (the owner of another organization, with their own API key) reaches for the
 *   customer's workspace, documents and members by URL and by API;
 * - an INSIDER (a viewer the customer invited) tries to raise their own role, mint an API key,
 *   invite someone and upload;
 * - a CHEAPER PLAN (the outsider's Developer organization) calls a Business feature directly.
 * Every attempt is refused by the server, and the browser shows a refusal screen, never the data
 * and never a crash.
 */
import fs from "node:fs";
import path from "node:path";

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api } from "../support/api";
import { API_BASE, runId } from "../support/env";
import { addMember, invoicePdf, newCustomer, signInAs, standIn, uploadViaApi } from "../support/journey";

const REFUSED = [401, 403, 404];

test.describe("Journey: an attacker", () => {
  test.setTimeout(300_000);

  test("cross-tenant access, role escalation and paywall bypass are all refused", async ({ page, problems }) => {
    const victim = await newCustomer("victim-journey", "Victim Holdings");
    standIn("subscribe", victim.orgSlug, "--plan", "business", "--seats", "3");
    const documentId = await uploadViaApi(victim, "victim-secret");
    const outsider = await newCustomer("outsider-journey", "Outsider Labs");
    standIn("subscribe", outsider.orgSlug, "--plan", "developer", "--seats", "1");
    const insider = await addMember(victim, "insider-journey", "MEMBER", "VIEWER");

    // The outsider, by URL in the browser: a refusal screen, not the victim's data.
    problems.allowHttp(/\/api\/v1\//, [401, 402, 403, 404], "the attacker's attempts are refused on purpose");
    await signInAs(page, outsider.email);
    await page.goto(victim.ws("work-items"));
    await expect(page.locator("body")).not.toContainText("victim-secret", { timeout: 10_000 });
    await expectHealthyPage(page);
    await page.goto(victim.org("members"));
    await expect(page.locator("body")).not.toContainText(victim.email);

    // The outsider, by API with their session and with their own API key.
    const key = await api<{ token?: string }>(outsider.session, "POST", `/organizations/${outsider.orgId}/api-keys`,
      { name: "Probe", scopes: ["workspaces:read", "work_items:read", "work_items:write"] });
    expect(key.status, key.text).toBe(201);
    for (const [method, url] of [
      ["GET", `/workspaces/${victim.wsId}/work-items`],
      ["GET", `/workspaces/${victim.wsId}/work-items/${documentId}`],
      ["GET", `/organizations/${victim.orgId}/members`],
      ["GET", `/organizations/${victim.orgId}/billing/subscription`],
      ["DELETE", `/workspaces/${victim.wsId}/work-items/${documentId}`],
    ] as const) {
      expect(REFUSED, `${method} ${url} as the outsider`).toContain((await api(outsider.session, method, url)).status);
      const withKey = await fetch(`${API_BASE}${url}`, { method, headers: { Authorization: `Bearer ${key.body.token}` } });
      expect(REFUSED, `${method} ${url} with the outsider's key`).toContain(withKey.status);
    }

    // The insider (a viewer) tries to raise their own role and to act beyond it.
    const members = await api<Array<{ id: string; user: { email: string } }> | { items: Array<{ id: string; user: { email: string } }> }>(
      victim.session, "GET", `/organizations/${victim.orgId}/members`);
    const list = Array.isArray(members.body) ? members.body : members.body.items;
    const own = list.find((m) => m.user.email === insider.email);
    expect(own, members.text).toBeTruthy();
    const attempts = [
      await api(insider.session, "PATCH", `/organizations/${victim.orgId}/members/${own!.id}`, { role: "ADMIN" }),
      await api(insider.session, "POST", `/organizations/${victim.orgId}/api-keys`, { name: "mine", scopes: ["work_items:read"] }),
      await api(insider.session, "POST", `/organizations/${victim.orgId}/invitations`,
        { email: `friend-${runId()}@e2e.example.com`, organization_role: "ADMIN", grants: [] }),
    ];
    for (const attempt of attempts) {
      expect(attempt.status, attempt.text).toBe(403);
    }
    const file = invoicePdf("insider-upload");
    const form = new FormData();
    form.append("file", new Blob([fs.readFileSync(file)], { type: "application/pdf" }), path.basename(file));
    expect((await api(insider.session, "POST", `/workspaces/${victim.wsId}/work-items`, form)).status).toBe(403);
    await signInAs(page, insider.email);
    await page.goto(victim.org("members"));
    await expect(page.getByRole("button", { name: "Send invitation" })).toHaveCount(0);
    await expectHealthyPage(page);

    // The cheaper plan calls a Business feature directly: payment required, with the way on.
    const cases = await api<{ code?: string; detail?: { code?: string } }>(outsider.session, "POST",
      `/workspaces/${outsider.wsId}/case-templates`, { key: "probe", name: "Probe", required_documents: [], rules: [] });
    expect(cases.status, cases.text).toBe(402);
    expect(cases.text).toMatch(/CAPABILITY_REQUIRED|PLAN_UPGRADE/);
    await signInAs(page, outsider.email);
    await page.goto(outsider.ws("cases"));
    await expect(page.locator("main")).toContainText(/not included in your plan|upgrade|View plans/i, { timeout: 15_000 });
    await expectHealthyPage(page);
  });
});
