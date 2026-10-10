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

/** The team table's row for one email, and its Actions cell. */
function teamRow(page: import("@playwright/test").Page, email: string) {
  const table = page.getByRole("table", { name: "Team members" });
  const row = table.getByRole("row").filter({ hasText: email });
  return { table, row, actions: row.getByRole("cell").last() };
}

test.describe("The Actions column of the workspace team (F-219)", () => {
  test.use({ user: "C.owner" });
  test.setTimeout(180_000);

  test("an admin gets role controls and Remove for each member, and a badge for themself and the owners", async ({ page }) => {
    await openGeneralSettings(page);
    const { table } = teamRow(page, "c-owner@e2e.example.com");
    await expect(table.getByRole("columnheader", { name: "Actions" })).toBeVisible();

    // Their own row (the organization owner, an admin through that role): a muted badge, no controls.
    const self = teamRow(page, "c-owner@e2e.example.com").actions;
    await expect(self).toContainText("Owner");
    await expect(self).toContainText("You");
    await expect(self.getByRole("combobox")).toHaveCount(0);
    await expect(self.getByRole("button")).toHaveCount(0);

    // Another organization admin: an admin through their organization role, changed there.
    const orgAdmin = teamRow(page, "c-admin@e2e.example.com").actions;
    await expect(orgAdmin).toContainText("Org admin");
    await expect(orgAdmin.getByRole("button", { name: /^Remove / })).toHaveCount(0);

    // A member with a workspace role: the role control and Remove, in the Actions cell.
    const viewer = teamRow(page, "c-viewer@e2e.example.com").actions;
    const select = viewer.getByRole("combobox", { name: "Workspace role for c-viewer@e2e.example.com" });
    await expect(select).toBeVisible();
    await expect(select.locator("option")).toHaveText(["Admin", "Contributor", "Viewer"]);
    await expect(select).toHaveValue("VIEWER");
    await expect(
      viewer.getByRole("button", { name: "Remove c-viewer@e2e.example.com from this workspace" }),
    ).toBeVisible();
  });

  test("the role control changes a member's role, and Remove takes them out of the workspace", async ({ page }) => {
    const { orgId, workspaceId } = await operations();
    const owner = await loginAs("C.owner");
    const joiner = `team-${runId()}@e2e.example.com`;
    const person = await signUpVerified(joiner);
    const since = Date.now() - 1_000;
    const invited = await api(owner, "POST", `/organizations/${orgId}/invitations`, {
      email: joiner,
      organization_role: "MEMBER",
      grants: [{ workspace_id: workspaceId, role: "VIEWER" }],
    });
    expect(invited.status, invited.text).toBe(201);
    const mail = await waitForMail(joiner, /invitations\/accept/, since);
    const accepted = await api(person, "POST", "/invitations/accept", {
      token: tokenOf(linkFrom(mail, /invitations\/accept/)),
    });
    expect(accepted.status, accepted.text).toBe(200);

    try {
      await openGeneralSettings(page);
      const { actions } = teamRow(page, joiner);
      const select = actions.getByRole("combobox", { name: `Workspace role for ${joiner}` });
      await expect(select).toHaveValue("VIEWER", { timeout: 15_000 });
      await select.selectOption("CONTRIBUTOR");
      await expect(page.getByText("Role updated to contributor.")).toBeVisible();
      await expect(select).toHaveValue("CONTRIBUTOR");

      type Members = { items: { id: string | null; role: string; user: { email: string } }[] };
      const afterChange = await api<Members>(owner, "GET", `/workspaces/${workspaceId}/members`);
      expect(afterChange.body.items.find((m) => m.user.email === joiner)?.role).toBe("CONTRIBUTOR");

      await actions.getByRole("button", { name: `Remove ${joiner} from this workspace` }).click();
      await page.getByRole("alertdialog").getByRole("button", { name: "Remove", exact: true }).click();
      await expect(page.getByText("Member removed successfully.")).toBeVisible();
      await expect(teamRow(page, joiner).row).toHaveCount(0);
      const afterRemoval = await api<Members>(owner, "GET", `/workspaces/${workspaceId}/members`);
      expect(afterRemoval.body.items.some((m) => m.user.email === joiner)).toBe(false);
    } finally {
      const members = await api<{ items: { id: string; user?: { email?: string } }[] }>(
        owner, "GET", `/organizations/${orgId}/members`,
      );
      const membership = members.body.items.find((m) => m.user?.email === joiner);
      if (membership) await api(owner, "POST", `/organizations/${orgId}/members/${membership.id}/deactivate`);
    }
  });
});

test.describe("The workspace team, seen by someone who cannot manage it (F-219)", () => {
  test.use({ user: "C.member" });

  test("no Actions column, and a way to leave the workspace", async ({ page }) => {
    await openTeamAsMember(page);
    const table = page.getByRole("table", { name: "Team members" });
    await expect(table.getByRole("row").filter({ hasText: "c-owner@e2e.example.com" })).toBeVisible();
    await expect(table.getByRole("columnheader", { name: "Actions" })).toHaveCount(0);
    await expect(table.getByRole("combobox")).toHaveCount(0);
    // Never clicked: the shared member must stay in the workspace for every other test.
    await expect(page.getByRole("button", { name: "Leave workspace" })).toBeVisible();
  });
});

async function openTeamAsMember(page: import("@playwright/test").Page): Promise<void> {
  await page.goto(ws("C", "settings"));
  await expect(async () => {
    await page.getByRole("button", { name: /^General Name, locale and members/ }).click();
    await expect(page.getByRole("heading", { name: "Team members" })).toBeVisible({ timeout: 3_000 });
  }).toPass({ timeout: 30_000 });
}
