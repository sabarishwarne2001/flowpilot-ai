/**
 * ROLE MATRIX. Roles in the code: organization OWNER / ADMIN / BILLING / MEMBER
 * and workspace ADMIN / CONTRIBUTOR / VIEWER. The seed's "member" is MEMBER +
 * CONTRIBUTOR and "viewer" is MEMBER + VIEWER.
 *
 *  - The sidebar shows each role only what it may use.
 *  - A page opened by URL by a role that may not use it shows a permission
 *    screen or redirects; it is never blank and never crashes. (The server's
 *    403s on such a page are expected and allowed.)
 *  - The server refuses every mutating action a role may not take (403),
 *    whatever the UI shows.
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, loginAs, resolveWorkspaceId, type ApiSession } from "../support/api";
import { TENANTS, org, runId, ws, type UserKey } from "../support/env";
import { LOCK_TEXT, ORGANIZATION_PAGES } from "../support/routes";

const PERMISSION_TEXT =
  /permission|not allowed|only (organization )?(owners|admins|administrators)|owners only|ask an? (organization )?(owner|admin)|requires? an? (organization )?(owner|admin|administrator)|don.t have access|no access|restricted|forbidden|isn.t available to your role/i;

const ORG_NAV_BY_ROLE: Record<string, { visible: string[]; hidden: string[] }> = {
  "A.owner": {
    visible: ["General", "Members", "Billing", "API keys", "Webhooks", "Audit log", "Developer platform"],
    hidden: [],
  },
  // N-006: OWNER and ADMIN may both mint API keys, so the sidebar shows API keys to ADMIN (F-015, fixed).
  // Decided in the final release: ADMIN also sees Webhooks, the audit log and Enterprise identity
  // (the API serves them to ADMIN; identity writes stay OWNER-only on the server).
  "A.admin": {
    visible: ["General", "Members", "Developer platform", "API keys", "Webhooks", "Audit log"],
    hidden: ["Billing"],
  },
  "A.billing": { visible: ["General", "Billing"], hidden: ["Members", "API keys", "Audit log"] },
  "A.member": {
    visible: ["General", "Notifications"],
    hidden: ["Members", "Billing", "API keys", "Webhooks", "Audit log", "Data governance & compliance"],
  },
  "A.viewer": {
    visible: ["General", "Notifications"],
    hidden: ["Members", "Billing", "API keys", "Webhooks", "Audit log", "Data governance & compliance"],
  },
};

for (const [user, expectation] of Object.entries(ORG_NAV_BY_ROLE)) {
  test.describe(`organization sidebar — ${user}`, () => {
    test.use({ user: user as UserKey });
    test(`shows exactly what ${user} may use`, async ({ page }) => {
      await page.goto(org("A", "settings"));
      await settle(page);
      const nav = page.getByLabel("Organization Navigation");
      for (const name of expectation.visible) {
        await expect.soft(nav.getByRole("link", { name, exact: true }), `${name} visible`).toBeVisible();
      }
      for (const name of expectation.hidden) {
        await expect.soft(nav.getByRole("link", { name, exact: true }), `${name} hidden`).toHaveCount(0);
      }
    });
  });
}

test.describe("workspace sidebar — viewer", () => {
  test.use({ user: "A.viewer" });
  test("Settings is hidden from a workspace VIEWER", async ({ page }) => {
    await page.goto(ws("A"));
    await settle(page);
    await expect(page.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Settings", exact: true })).toHaveCount(0);
  });

  test("Workflows, Run history and Review queue are hidden from a VIEWER (N-019)", async ({ page }) => {
    await page.goto(ws("A"));
    await settle(page);
    const nav = page.getByRole("navigation", { name: "Primary Navigation" });
    await expect(nav.getByRole("link", { name: "Documents", exact: true })).toBeVisible();
    for (const name of ["Workflows", "Run history", "Review queue"]) {
      await expect(nav.getByRole("link", { name, exact: true }), `${name} hidden`).toHaveCount(0);
    }
  });
});

/** Organization pages a MEMBER may not use, opened by URL. */
const ADMIN_ONLY_ORG_PAGES = ORGANIZATION_PAGES.filter(
  (p) => !["org:general", "org:notifications"].includes(p.navId),
);

for (const user of ["A.member", "A.viewer"] as const) {
  test.describe(`forbidden organization pages by URL — ${user}`, () => {
    test.use({ user });
    for (const target of ADMIN_ONLY_ORG_PAGES) {
      test(`${target.id} shows a permission screen or redirects`, async ({ page, problems }) => {
        problems.allowHttp(/\/api\/v1\/organizations\//, [402, 403], "the server refuses this role");
        await page.goto(org("A", target.sub));
        await settle(page, 1000);
        const redirected = !page.url().includes(`/${target.sub}`);
        if (!redirected) {
          // A plan lock card also tells the reader they cannot use the page.
          const allowed = new RegExp(`${PERMISSION_TEXT.source}|${LOCK_TEXT.source}`, "i");
          await expect(page.locator("main"), "a permission message, not a blank page").toContainText(allowed);
        }
        await expectHealthyPage(page);
      });
    }
  });
}

test.describe("workspace pages shown to a VIEWER must work for a VIEWER", () => {
  test.use({ user: "C.viewer" });
  for (const sub of ["", "work-items", "assistant", "automation", "automation/timeline", "verification", "notifications"]) {
    test(`viewer can open ${sub || "overview"} without errors`, async ({ page }) => {
      await page.goto(ws("C", sub));
      await settle(page, 1000);
      await expectHealthyPage(page);
      await expect(page.locator("main")).not.toContainText(/failed to load|could not be loaded|something went wrong/i);
    });
  }
});

test.describe("platform pages reject tenant admins", () => {
  for (const user of ["C.owner", "C.admin", "A.owner"] as const) {
    test.describe(user, () => {
      test.use({ user });
      for (const path of ["/admin/margins", "/admin/sovereign", "/admin/revops"]) {
        test(`${path} is not shown to ${user}`, async ({ page }) => {
          await page.goto(path);
          await settle(page);
          await expect(page).not.toHaveURL(new RegExp(`${path}$`));
          await expect(page.locator("body")).not.toContainText(/Unit economics|Sovereign edition|Revenue operations/);
        });
      }
    });
  }

  test("the platform API refuses a tenant owner and admin, and serves the super-admin", async () => {
    const superadmin = await loginAs("P.superadmin");
    const probes = ["/admin/cogs/margins/summary", "/admin/sovereign", "/admin/revops/metrics"];
    const found: string[] = [];
    for (const user of ["C.owner", "C.admin"] as const) {
      const tenantAdmin = await loginAs(user);
      for (const probe of probes) {
        const refused = await api(tenantAdmin, "GET", probe);
        found.push(`${probe}: ${user}=${refused.status}`);
        // The platform routes answer 404 (not 403) to non-operators, so they do not
        // reveal that they exist. Either is a refusal; anything else is a leak.
        expect.soft([403, 404], `${probe} as ${user}`).toContain(refused.status);
      }
    }
    for (const probe of probes) {
      const served = await api(superadmin, "GET", probe);
      found.push(`${probe}: superadmin=${served.status}`);
      expect.soft(served.status, `${probe} as super-admin`).toBe(200);
    }
    test.info().annotations.push({ type: "platform-probes", description: found.join("; ") });
  });
});

async function tenantIds(session: ApiSession, tenant: "A" | "C") {
  return resolveWorkspaceId(session, TENANTS[tenant].org, TENANTS[tenant].ws);
}

test.describe("mutations a role may not take are refused by the server", () => {
  test("organization profile: only OWNER and ADMIN may edit it", async () => {
    const expected: Record<string, number[]> = {
      "A.owner": [200],
      "A.admin": [200],
      "A.billing": [403],
      "A.member": [403],
      "A.viewer": [403],
    };
    for (const [user, statuses] of Object.entries(expected)) {
      const session = await loginAs(user as UserKey);
      const { organizationId } = await tenantIds(session, "A");
      const response = await api(session, "PATCH", `/organizations/${organizationId}`, { name: TENANTS.A.name });
      expect.soft(statuses, `${user} PATCH organization -> ${response.status} ${response.text.slice(0, 120)}`).toContain(
        response.status,
      );
    }
  });

  test("API keys: OWNER and ADMIN may create and revoke them (N-006); nobody else may", async () => {
    const owner = await loginAs("A.owner");
    const { organizationId } = await tenantIds(owner, "A");
    const created: string[] = [];
    for (const [user, allowed] of [
      ["A.owner", true],
      ["A.admin", true],
      ["A.billing", false],
      ["A.member", false],
      ["A.viewer", false],
    ] as const) {
      const session = await loginAs(user);
      const response = await api<{ id?: string; api_key?: { id: string } }>(
        session,
        "POST",
        `/organizations/${organizationId}/api-keys`,
        { name: `e2e role ${user} ${runId()}`, scopes: ["work_items:read"] },
      );
      if (allowed) {
        expect.soft([200, 201], `${user} create key -> ${response.status} ${response.text.slice(0, 160)}`).toContain(
          response.status,
        );
        const id = response.body?.id ?? response.body?.api_key?.id;
        if (id) created.push(id);
      } else {
        expect.soft(response.status, `${user} create key -> ${response.text.slice(0, 160)}`).toBe(403);
      }
    }
    // Revocation: refused for MEMBER, allowed for OWNER (cleans up).
    const member = await loginAs("A.member");
    for (const id of created) {
      const refused = await api(member, "DELETE", `/organizations/${organizationId}/api-keys/${id}`);
      expect.soft(refused.status, "member revoke").toBe(403);
      const revoked = await api(owner, "DELETE", `/organizations/${organizationId}/api-keys/${id}`);
      expect.soft([200, 204], `owner revoke -> ${revoked.status}`).toContain(revoked.status);
    }
  });

  test("ERP posting: a workspace VIEWER and a MEMBER may not post; the server says 403", async () => {
    for (const user of ["C.viewer", "C.member"] as const) {
      const session = await loginAs(user);
      const { workspaceId } = await tenantIds(session, "C");
      const response = await api(session, "POST", `/workspaces/${workspaceId}/erp/postings`, {
        target_id: "00000000-0000-4000-8000-000000000001",
        source_kind: "WORK_ITEM",
        source_id: "00000000-0000-4000-8000-000000000002",
        object_kinds: ["VENDOR_BILL"],
      });
      expect.soft(response.status, `${user} POST erp/postings -> ${response.text.slice(0, 200)}`).toBe(403);
    }
  });

  test("ERP targets: a workspace VIEWER may not create one", async () => {
    const session = await loginAs("C.viewer");
    const { workspaceId } = await tenantIds(session, "C");
    const response = await api(session, "POST", `/workspaces/${workspaceId}/erp/targets`, {
      name: `viewer target ${runId()}`,
      kind: "CSV",
    });
    expect(response.status, response.text.slice(0, 200)).toBe(403);
  });
});

test.describe("organization settings are read-only for a MEMBER in the UI", () => {
  test.use({ user: "A.member" });
  test("the organization name cannot be edited", async ({ page }) => {
    await page.goto(org("A", "settings"));
    await settle(page);
    const name = page.locator("#org-name");
    if ((await name.count()) > 0) {
      await expect(name).toBeDisabled();
    }
    await expect(page.getByRole("button", { name: "Archive organization" })).toHaveCount(0);
  });
});
