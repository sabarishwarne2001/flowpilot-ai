/**
 * Campaign session 3 (N-062) — notifications reach people by email, and each person chooses
 * which. Driven the way a person uses it: the Settings panel in the browser, and a real change
 * whose email travels API → notification outbox → worker → platform relay → the mail sink.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api, resolveWorkspaceId } from "../support/api";
import { TENANTS, USERS, org, runId, ws } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";
import { signUpVerified, tokenOf } from "../support/accounts";

const PREFERENCES = "/me/notification-preferences";

interface Category {
  readonly key: string;
  readonly email: boolean;
  readonly locked: boolean;
}

test.describe("Notification email choices", () => {
  test.use({ user: "C.owner" });

  test("security and billing are always emailed; usage warnings can be turned off and on", async ({ page, session }) => {
    const reset = () => api(session, "PUT", PREFERENCES, { category: "usage", email: true });
    expect((await reset()).status).toBe(200);
    try {
      await page.goto(`${ws("C", "settings")}?section=notifications`);
      const list = page.getByRole("list", { name: "Email notifications" });
      await expect(list).toBeVisible();
      for (const always of ["Security", "Billing"]) {
        await expect(list.getByRole("listitem").filter({ hasText: always })).toContainText("Always emailed");
      }
      const usage = list.getByRole("switch", { name: "Usage warnings" });
      await expect(usage).toHaveAttribute("aria-checked", "true");
      await usage.click();
      await expect(usage).toHaveAttribute("aria-checked", "false");
      await expect
        .poll(async () => (await api<{ categories: Category[] }>(session, "GET", PREFERENCES)).body.categories.find((c) => c.key === "usage")?.email)
        .toBe(false);

      await page.reload();
      await expect(page.getByRole("switch", { name: "Usage warnings" })).toHaveAttribute("aria-checked", "false");
      await page.getByRole("switch", { name: "Usage warnings" }).click();
      await expect(page.getByRole("switch", { name: "Usage warnings" })).toHaveAttribute("aria-checked", "true");
      await expectHealthyPage(page);
    } finally {
      await reset();
    }
  });
});

test.describe("Security notices by email", () => {
  test.setTimeout(180_000);

  test("a member whose role changes is told by email, through the worker and the platform relay", async () => {
    const id = runId();
    const ownerEmail = `notice-owner-${id}@e2e.example.com`;
    const memberEmail = `notice-member-${id}@e2e.example.com`;
    const owner = await signUpVerified(ownerEmail);
    const member = await signUpVerified(memberEmail);
    const name = `Notice Co ${id}`;
    const created = await api<{ id: string }>(owner, "POST", "/organizations", {
      organization_name: name,
      organization_slug: `notice-${id}`.toLowerCase().slice(0, 40),
    });
    expect(created.status, created.text).toBeLessThan(300);
    const orgId = created.body.id;
    const since = Date.now() - 1_000;
    const invited = await api(owner, "POST", `/organizations/${orgId}/invitations`, { email: memberEmail, organization_role: "MEMBER" });
    expect(invited.status, invited.text).toBeLessThan(300);
    const invitation = await waitForMail(memberEmail, /invitations\/accept/, since);
    const accepted = await api(member, "POST", "/invitations/accept", { token: tokenOf(linkFrom(invitation, /invitations\/accept/)) });
    expect(accepted.status, accepted.text).toBeLessThan(300);

    const members = await api<{ items: { id: string; user: { email: string } }[] }>(owner, "GET", `/organizations/${orgId}/members`);
    expect(members.status, members.text).toBe(200);
    const membership = members.body.items.find((row) => row.user.email.toLowerCase() === memberEmail);
    expect(membership, members.text).toBeTruthy();

    const changedAt = Date.now() - 1_000;
    const changed = await api(owner, "PATCH", `/organizations/${orgId}/members/${membership!.id}`, { role: "ADMIN" });
    expect(changed.status, changed.text).toBe(200);

    const mail = await waitForMail(memberEmail, new RegExp(`Your role in ${name} changed`), changedAt, 90_000);
    expect(mail.text).toContain("from MEMBER to ADMIN");
    expect(mail.text).toContain("cannot be turned off");
    expect(mail.text).toContain("/settings?section=notifications");
  });
});

// ---------------------------------------------------------------------------------------------
// Access screens name the people who can grant access (HANDOFF carry-over, campaign session 3).
// ---------------------------------------------------------------------------------------------
interface Person {
  readonly role: string;
  readonly status: string;
  readonly user: { readonly id: string; readonly email: string; readonly display_name?: string | null };
}

const nameOf = (person: Person): string => person.user.display_name?.trim() || person.user.email;

test.describe("Access restricted names who to ask (workspace)", () => {
  test.use({ user: "C.viewer" });

  test("a viewer on a workspace settings section is told which administrators to ask", async ({ page, session }) => {
    const { workspaceId } = await resolveWorkspaceId(session!, TENANTS.C.org, TENANTS.C.ws);
    const people = await api<{ items: Person[] }>(session, "GET", `/workspaces/${workspaceId}/members`);
    const admins = people.body.items
      .filter((p) => p.role === "ADMIN" && p.status === "ACTIVE" && p.user.email !== USERS["C.viewer"].email)
      .map(nameOf);
    expect(admins.length).toBeGreaterThan(0);

    await page.goto(`${ws("C", "settings")}?section=workspace`);
    const ask = page.getByTestId("access-restricted-ask");
    await expect(ask).toContainText(admins[0]!, { timeout: 15_000 });
    await expect(ask).not.toContainText("a workspace administrator");
  });
});

test.describe("Access restricted names who to ask (organization)", () => {
  test.use({ user: "C.member" });

  test("a member on the identity console is told which owner to ask", async ({ page, session }) => {
    const { organizationId } = await resolveWorkspaceId(session!, TENANTS.C.org, TENANTS.C.ws);
    const people = await api<{ items: Person[] }>(session, "GET", `/organizations/${organizationId}/members`);
    const owners = people.body.items.filter((p) => p.role === "OWNER" && p.status === "ACTIVE").map(nameOf);
    expect(owners.length).toBeGreaterThan(0);

    await page.goto(org("C", "identity"));
    const ask = page.getByTestId("access-restricted-ask");
    await expect(ask).toContainText(owners[0]!, { timeout: 15_000 });
    await expect(ask).not.toContainText("an organization owner");
  });
});
