/**
 * ACCEPTING AN INVITATION, from the email, the way an invitee does it.
 *
 *  - Someone signed in as another account who switches account is really signed
 *    out: the old session no longer refreshes (F-224).
 */
import { test, expect } from "../support/fixtures";
import { api, loginAs } from "../support/api";
import { BROWSER_API_ORIGIN, runId } from "../support/env";
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
