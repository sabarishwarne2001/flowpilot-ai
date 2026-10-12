/**
 * Campaign session 3 — the identity console's security controls do what they say, driven the
 * way an owner uses them. Each test is named after its finding (docs/hardening/FINDINGS.md,
 * "Pre-launch campaign, session 3").
 */
import { ProblemTracker, attachProblemListeners, test, expect, expectHealthyPage } from "../support/fixtures";
import { api, resolveWorkspaceId } from "../support/api";
import { PASSWORD, TENANTS, org, runId } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";
import { openAs, signUpVerified, tokenOf } from "../support/accounts";
import { totpCode } from "../support/totp";

test.describe("Allowed networks on the identity console (F-277)", () => {
  test.use({ user: "C.owner" });

  test("the owner sees their address, cannot save a list that leaves them out, and can allow their own network", async ({
    page,
    session,
    problems,
  }) => {
    const { organizationId: orgId } = await resolveWorkspaceId(session!, TENANTS.C.org, TENANTS.C.ws);
    const policyPath = `/organizations/${orgId}/identity/security-policy`;
    const reset = () => api(session, "PUT", policyPath, { ip_allowlist: [] });
    expect((await reset()).status).toBe(200);
    problems.allowHttp(/\/identity\/security-policy/, [409], "a list that would lock the owner out is refused");

    try {
      await page.goto(`${org("C", "identity")}?tab=security`);
      const field = page.getByLabel("Allowed networks");
      await expect(field).toBeVisible();
      const main = page.locator("main");
      // The browser reaches the API through the preview server on this machine.
      await expect(main).toContainText("Your address now: 127.0.0.1");

      await field.fill("203.0.113.0/24");
      await page.getByRole("button", { name: "Save allowed networks" }).click();
      await expect(page.getByRole("alert")).toContainText("lock you out");
      const untouched = await api<{ ip_allowlist: string[] }>(session, "GET", policyPath);
      expect(untouched.body.ip_allowlist).toEqual([]);

      await field.fill("127.0.0.0/8\n203.0.113.0/24");
      await page.getByRole("button", { name: "Save allowed networks" }).click();
      await expect
        .poll(async () => (await api<{ ip_allowlist: string[] }>(session, "GET", policyPath)).body.ip_allowlist)
        .toEqual(["127.0.0.0/8", "203.0.113.0/24"]);

      await page.reload();
      await expect(page.getByLabel("Allowed networks")).toHaveValue("127.0.0.0/8\n203.0.113.0/24");
      // Still inside the list, so the organization keeps working for this owner.
      await page.goto(org("C", "members"));
      await expect(page.locator("main")).toBeVisible();

      await page.goto(`${org("C", "identity")}?tab=security`);
      await page.getByLabel("Allowed networks").fill("");
      await page.getByRole("button", { name: "Allow every network" }).click();
      await expect
        .poll(async () => (await api<{ ip_allowlist: string[] }>(session, "GET", policyPath)).body.ip_allowlist)
        .toEqual([]);
      await expectHealthyPage(page);
    } finally {
      await reset();
    }
  });
});

// ---------------------------------------------------------------------------------------------
// Requiring two-factor sign-in (N-017, decided again in session 3), between two people who sign
// up for this test, so no seeded account is asked for a second factor while other tests run.
// ---------------------------------------------------------------------------------------------
test.describe("Requiring two-factor sign-in", () => {
  test.setTimeout(240_000);

  test("an owner requires it; a member without it sets it up from the notice and carries on", async ({ browser }) => {
    const id = runId();
    const ownerEmail = `mfa-owner-${id}@e2e.example.com`;
    const memberEmail = `mfa-member-${id}@e2e.example.com`;
    const owner = await signUpVerified(ownerEmail);
    const member = await signUpVerified(memberEmail);
    const slug = `mfa-${id}`.toLowerCase().slice(0, 40);
    const created = await api<{ id: string }>(owner, "POST", "/organizations", {
      organization_name: `MFA Co ${id}`,
      organization_slug: slug,
    });
    expect(created.status, created.text).toBeLessThan(300);
    const orgId = created.body.id;
    const since = Date.now() - 1_000;
    const invited = await api(owner, "POST", `/organizations/${orgId}/invitations`, { email: memberEmail, organization_role: "MEMBER" });
    expect(invited.status, invited.text).toBeLessThan(300);
    const invitation = await waitForMail(memberEmail, /invitations\/accept/, since);
    const accepted = await api(member, "POST", "/invitations/accept", { token: tokenOf(linkFrom(invitation, /invitations\/accept/)) });
    expect(accepted.status, accepted.text).toBeLessThan(300);

    // The owner turns on two-factor for their own account (required before requiring it).
    const setup = await api<{ secret: string }>(owner, "POST", "/me/mfa/setup", { password: PASSWORD });
    expect(setup.status, setup.text).toBe(200);
    const confirmed = await api(owner, "POST", "/me/mfa/confirm", { code: totpCode(setup.body.secret) });
    expect(confirmed.status, confirmed.text).toBe(200);

    // The owner requires it from General, after seeing who will be asked.
    const ownerSide = await openAs(browser, owner);
    const ownerProblems = new ProblemTracker();
    attachProblemListeners(ownerSide.page, ownerProblems);
    await ownerSide.page.goto(`/organizations/${slug}/settings`);
    const card = ownerSide.page.locator("section", { hasText: "Two-factor sign-in" });
    await expect(card.getByRole("list", { name: "Members without two-factor sign-in" })).toContainText(memberEmail);
    await card.getByRole("button", { name: "Require two-factor sign-in…" }).click();
    await card.getByRole("button", { name: "Require two-factor sign-in", exact: true }).click();
    await expect(card).toContainText("Required for every member.");

    // The member is stopped by one notice, sets it up inside it, and carries on.
    const memberSide = await openAs(browser, member);
    const memberProblems = new ProblemTracker();
    memberProblems.allowHttp(/\/api\/v1\/(workspaces|organizations)\//, [403], "the organization requires two-factor sign-in");
    attachProblemListeners(memberSide.page, memberProblems);
    // A member without a workspace grant: the organization's Members page is theirs to open.
    await memberSide.page.goto(`/organizations/${slug}/members`);
    const notice = memberSide.page.getByRole("dialog", { name: "Two-factor sign-in required" });
    await expect(notice).toBeVisible({ timeout: 20_000 });
    const panel = notice.getByRole("region", { name: "Two-factor sign-in" });
    await panel.getByRole("button", { name: "Turn on two-factor sign-in" }).click();
    await panel.getByLabel("Current password").fill(PASSWORD);
    await panel.getByRole("button", { name: "Continue" }).click();
    const secret = (await panel.getByTestId("mfa-secret").innerText()).replace(/\s/g, "");
    await panel.getByLabel("6-digit code from the app").fill(totpCode(secret));
    await panel.getByRole("button", { name: "Turn on", exact: true }).click();
    await panel.getByRole("button", { name: "I have saved them" }).click();
    await notice.getByRole("button", { name: "Continue", exact: true }).last().click();
    await expect(memberSide.page.getByRole("dialog", { name: "Two-factor sign-in required" })).toHaveCount(0, { timeout: 20_000 });
    await expect(memberSide.page.locator("main")).toContainText(memberEmail, { timeout: 20_000 });

    expect(ownerProblems.unexpected(), ownerProblems.summary()).toEqual([]);
    expect(memberProblems.unexpected(), memberProblems.summary()).toEqual([]);
    await ownerSide.context.close();
    await memberSide.context.close();
  });
});
