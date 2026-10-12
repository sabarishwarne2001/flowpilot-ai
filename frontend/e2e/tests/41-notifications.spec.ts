/**
 * Campaign session 3 (N-062) — notifications reach people by email, and each person chooses
 * which. Driven the way a person uses it: the Settings panel in the browser, and a real change
 * whose email travels API → notification outbox → worker → platform relay → the mail sink.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api } from "../support/api";
import { runId, ws } from "../support/env";
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
