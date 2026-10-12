/**
 * Campaign session 3 (N-064) — one display layer. A workspace's date format decides how the dates
 * on its documents read (here: an obligation's due date), for everyone in it; the server's tenant
 * context carries the setting, and the page re-renders when it changes. Run against C's second
 * workspace so the main one's settings never move under parallel tests.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api, resolveWorkspaceId } from "../support/api";
import { C_SECOND_WS, TENANTS, runId } from "../support/env";

test.describe("Workspace date format", () => {
  test.use({ user: "C.owner" });

  test("an obligation's due date reads in the workspace's date format", async ({ page, session }) => {
    const { workspaceId } = await resolveWorkspaceId(session!, TENANTS.C.org, C_SECOND_WS);
    const before = await api<{ date_format: string }>(session, "GET", `/workspaces/${workspaceId}`);
    expect(before.status, before.text).toBe(200);
    const title = `Display format check ${runId()}`;
    const created = await api<{ id?: string; obligation?: { id: string } }>(session, "POST", `/workspaces/${workspaceId}/obligations`, {
      kind: "PAYMENT", title, rule: { kind: "FIXED", date: "2026-12-15" },
    });
    expect(created.status, created.text).toBeLessThan(300);
    const obligationId = created.body.obligation?.id ?? created.body.id;
    const listPath = `/${TENANTS.C.org}/${C_SECOND_WS}/obligations`;

    try {
      for (const [format, shown] of [["DD/MM/YYYY", "15/12/2026"], ["MM/DD/YYYY", "12/15/2026"]] as const) {
        const set = await api(session, "PATCH", `/workspaces/${workspaceId}`, { date_format: format });
        expect(set.status, set.text).toBe(200);
        await page.goto(listPath);
        await page.reload();
        const row = page.getByRole("row").filter({ hasText: title });
        await expect(row).toContainText(shown, { timeout: 20_000 });
      }
      await expectHealthyPage(page);
    } finally {
      await api(session, "PATCH", `/workspaces/${workspaceId}`, { date_format: before.body.date_format });
      if (obligationId) {
        await api(session, "DELETE", `/workspaces/${workspaceId}/obligations/${obligationId}`);
      }
    }
  });
});
