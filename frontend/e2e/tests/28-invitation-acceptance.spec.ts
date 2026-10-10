/**
 * ACCEPTING AN INVITATION, from the email, the way an invitee does it.
 *
 *  - Someone with no account gets a sign-up card for the inviting organization:
 *    the invited address filled in and locked, a password, and one click that
 *    creates the account, joins and opens the workspace (F-222).
 *  - Someone who already has an account is asked to sign in (the address filled
 *    in), comes back to the invitation and joins (F-222).
 *  - Someone signed in as another account who switches account is really signed
 *    out: the old session no longer refreshes (F-224).
 */
import { test, expect } from "../support/fixtures";
import { api, apiLogin, loginAs } from "../support/api";
import { signUpVerified } from "../support/accounts";
import { BROWSER_API_ORIGIN, PASSWORD, runId, ws } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";

interface MyWorkspace {
  readonly id: string;
  readonly slug: string;
  readonly organization_id: string;
  readonly organization_slug: string;
  readonly role?: string;
}

async function operations(): Promise<{ orgId: string; workspaceId: string }> {
  const owner = await loginAs("C.owner");
  const mine = await api<MyWorkspace[]>(owner, "GET", "/me/workspaces");
  const row = mine.body.find((w) => w.organization_slug === "caretakers-global" && w.slug === "operations");
  if (!row) throw new Error("Tenant C operations workspace not found");
  return { orgId: row.organization_id, workspaceId: row.id };
}

/** Invite `email` to Caretakers Global as a member with viewer access to Operations; the accept link (a path). */
async function invite(email: string): Promise<string> {
  const { orgId, workspaceId } = await operations();
  const owner = await loginAs("C.owner");
  const since = Date.now() - 1_000;
  const invited = await api(owner, "POST", `/organizations/${orgId}/invitations`, {
    email,
    organization_role: "MEMBER",
    grants: [{ workspace_id: workspaceId, role: "VIEWER" }],
  });
  expect(invited.status, invited.text).toBe(201);
  return linkFrom(await waitForMail(email, /invitations\/accept/, since), /invitations\/accept/);
}

async function removeFromOrganization(email: string): Promise<void> {
  const { orgId } = await operations();
  const owner = await loginAs("C.owner");
  const members = await api<{ items: { id: string; user?: { email?: string } }[] }>(
    owner, "GET", `/organizations/${orgId}/members`,
  );
  const membership = members.body.items.find((m) => m.user?.email === email);
  if (membership) await api(owner, "POST", `/organizations/${orgId}/members/${membership.id}/deactivate`);
}

test.describe("Accepting an invitation (F-222)", () => {
  test.setTimeout(180_000);

  test("someone with no account signs up from the invitation and lands in the workspace", async ({ page }) => {
    const email = `newcomer-${runId()}@e2e.example.com`;
    const path = await invite(email);
    try {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: "Join Caretakers Global Inc" })).toBeVisible({ timeout: 20_000 });
      await expect(page.getByText("c-owner@e2e.example.com")).toBeVisible();
      await expect(page.getByRole("listitem").filter({ hasText: "Operations" })).toContainText("Viewer");

      const address = page.getByLabel("Email");
      await expect(address).toHaveValue(email);
      await expect(address).not.toBeEditable();

      await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
      await page.getByLabel("Confirm password").fill(PASSWORD);
      await page.getByRole("button", { name: "Create account and join" }).click();

      await expect(page).toHaveURL(new RegExp(`${ws("C")}(/|$)`), { timeout: 20_000 });
      const person = await apiLogin(email);
      const mine = await api<MyWorkspace[]>(person, "GET", "/me/workspaces");
      expect(mine.body.map((w) => `${w.organization_slug}/${w.slug}`)).toEqual(["caretakers-global/operations"]);
    } finally {
      await removeFromOrganization(email);
    }
  });

  test("someone with an account signs in, comes back to the invitation and joins", async ({ page }) => {
    const email = `returning-${runId()}@e2e.example.com`;
    await signUpVerified(email);
    const path = await invite(email);
    try {
      await page.goto(path);
      await expect(page.getByRole("heading", { name: "Sign in to join Caretakers Global Inc" })).toBeVisible({
        timeout: 20_000,
      });
      await expect(page.getByRole("button", { name: "Create account and join" })).toHaveCount(0);
      await page.getByRole("button", { name: `Sign in as ${email}` }).click();

      await expect(page).toHaveURL(/\/login/);
      await expect(page.getByRole("textbox", { name: "Email" })).toHaveValue(email);
      await page.getByRole("button", { name: "Continue" }).click();
      await page.locator("#password").fill(PASSWORD);
      await page.locator("#password").press("Enter");

      await expect(page.getByRole("heading", { name: "Join Caretakers Global Inc" })).toBeVisible({ timeout: 20_000 });
      await page.getByRole("button", { name: "Accept and join" }).click();
      await expect(page).toHaveURL(new RegExp(`${ws("C")}(/|$)`), { timeout: 20_000 });
    } finally {
      await removeFromOrganization(email);
    }
  });
});

test.describe("Switching account from an invitation (F-224)", () => {
  test.use({ user: "C.viewer" });
  test.setTimeout(120_000);

  test("signs the other account out on the server, not only in this tab", async ({ page, problems }) => {
    problems.allowHttp(/\/auth\/refresh/, [401], "the session that was signed out must not refresh");
    const email = `someone-else-${runId()}@e2e.example.com`;
    const path = await invite(email);

    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Wrong account" })).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: "Sign out and switch account" }).click();
    await expect(page).toHaveURL(/\/login/);

    const refreshed = await page.request.post(`${BROWSER_API_ORIGIN}/api/v1/auth/refresh`);
    expect(refreshed.status(), "the signed-out session still refreshes").toBe(401);
  });
});
