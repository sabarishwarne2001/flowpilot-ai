/**
 * INVITATIONS AND THE WORKSPACE TEAM, driven the way people use them, with real mail.
 * Each test named after a finding (F-2xx) failed on the code before its fix
 * (docs/hardening/FINDINGS.md, "Invitations and the workspace team").
 */
import { test, expect } from "../support/fixtures";
import { api, loginAs } from "../support/api";
import { signUpVerified, tokenOf } from "../support/accounts";
import { runId, ws } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";

interface MyWorkspace {
  readonly id: string;
  readonly slug: string;
  readonly organization_id: string;
  readonly organization_slug: string;
}

async function operations(): Promise<{ orgId: string; workspaceId: string }> {
  const owner = await loginAs("C.owner");
  const mine = await api<MyWorkspace[]>(owner, "GET", "/me/workspaces");
  const row = mine.body.find((w) => w.organization_slug === "caretakers-global" && w.slug === "operations");
  if (!row) throw new Error("Tenant C operations workspace not found");
  return { orgId: row.organization_id, workspaceId: row.id };
}

async function openGeneralSettings(page: import("@playwright/test").Page): Promise<void> {
  await page.goto(ws("C", "settings"));
  await expect(async () => {
    await page.getByRole("button", { name: /^General Name, locale and members/ }).click();
    await expect(page.getByRole("heading", { name: "Pending invitations" })).toBeVisible({ timeout: 3_000 });
  }).toPass({ timeout: 30_000 });
}

test.describe("Pending invitations on the workspace settings page (F-216)", () => {
  test.use({ user: "C.owner" });
  test.setTimeout(180_000);

  test("an accepted invitation is never listed as pending, before or after the member is removed", async ({ page }) => {
    const { orgId, workspaceId } = await operations();
    const owner = await loginAs("C.owner");
    const joiner = `joiner-${runId()}@e2e.example.com`;
    const waiting = `waiting-${runId()}@e2e.example.com`;
    const person = await signUpVerified(joiner);

    const since = Date.now() - 1_000;
    const invited = await api(owner, "POST", `/organizations/${orgId}/invitations`, {
      email: joiner,
      organization_role: "MEMBER",
      grants: [{ workspace_id: workspaceId, role: "CONTRIBUTOR" }],
    });
    expect(invited.status, invited.text).toBe(201);
    const mail = await waitForMail(joiner, /invitations\/accept/, since);
    const accepted = await api(person, "POST", "/invitations/accept", {
      token: tokenOf(linkFrom(mail, /invitations\/accept/)),
    });
    expect(accepted.status, accepted.text).toBe(200);
    const stillWaiting = await api(owner, "POST", `/organizations/${orgId}/invitations`, {
      email: waiting,
      organization_role: "MEMBER",
      grants: [],
    });
    expect(stillWaiting.status, stillWaiting.text).toBe(201);

    await openGeneralSettings(page);
    const pendingSection = page.getByRole("heading", { name: "Pending invitations" }).locator("xpath=..");
    await expect(pendingSection).toContainText(waiting, { timeout: 15_000 });
    await expect(pendingSection).not.toContainText(joiner);
    await expect(pendingSection.getByRole("button", { name: `Resend the invitation to ${waiting}` })).toBeVisible();
    await expect(pendingSection.getByRole("button", { name: `Revoke the invitation to ${waiting}` })).toBeVisible();

    // The owner removes the person who joined; their accepted invitation stays history.
    const members = await api<{ items: { id: string; user?: { email?: string } }[] }>(
      owner, "GET", `/organizations/${orgId}/members`,
    );
    const membership = members.body.items.find((m) => m.user?.email === joiner);
    expect(membership, "the person who accepted is a member").toBeTruthy();
    const removed = await api(owner, "POST", `/organizations/${orgId}/members/${membership!.id}/deactivate`);
    expect(removed.status, removed.text).toBe(200);

    await openGeneralSettings(page);
    await expect(pendingSection).toContainText(waiting, { timeout: 15_000 });
    await expect(pendingSection).not.toContainText(joiner);

    const revoked = await api(owner, "GET", `/organizations/${orgId}/invitations?status=PENDING`);
    expect(revoked.text).not.toContain(joiner);
  });
});
