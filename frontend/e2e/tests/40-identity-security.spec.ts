/**
 * Campaign session 3 — the identity console's security controls do what they say, driven the
 * way an owner uses them. Each test is named after its finding (docs/hardening/FINDINGS.md,
 * "Pre-launch campaign, session 3").
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api, resolveWorkspaceId } from "../support/api";
import { TENANTS, org } from "../support/env";

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
