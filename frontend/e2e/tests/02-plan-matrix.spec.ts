/**
 * PLAN MATRIX. The tiers are the ones backend/scripts/seed_quota_tiers.py
 * publishes (confirmed by the owner, N-011):
 *   Developer  + developer API, webhooks, custom branding
 *   Business   + extraction memory, entity graph, cases/packets, tables,
 *                obligations, three-way matching, ERP posting, audit radar,
 *                custom email, custom-domain and warehouse add-ons
 *   Enterprise + process intelligence, corroborator, clause assertions,
 *                calibrated autonomy, egress lockdown, enterprise identity,
 *                redaction, collaborative review, priority SLO
 *
 * For every locked item: the sidebar shows a lock and opens the upgrade dialog
 * ("View plans" leads to Billing), the page itself shows a lock card when
 * opened by URL, and the API refuses with 402. On the plan that includes it,
 * the row is a plain link, the page renders the feature, and the API answers 200.
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, loginAs, resolveWorkspaceId } from "../support/api";
import { TENANTS, org, ws, type TenantKey, type UserKey } from "../support/env";
import { LOCK_TEXT, NAV_LABEL, ORGANIZATION_PAGES, WORKSPACE_PAGES } from "../support/routes";

type Plan = "developer" | "business" | "enterprise";
const RANK: Record<Plan | "any", number> = { any: 0, developer: 1, business: 2, enterprise: 3 };

/** One read the page needs, per gated workspace feature. */
const WS_PROBES: Record<string, { path: string; plan: Plan }> = {
  "ws:extraction-memory": { path: "extraction-memory/summary", plan: "business" },
  "ws:entities": { path: "entities", plan: "business" },
  "ws:cases": { path: "cases", plan: "business" },
  "ws:packet-splits": { path: "packet-splits", plan: "business" },
  "ws:tables": { path: "tables", plan: "business" },
  "ws:obligations": { path: "obligations", plan: "business" },
  "ws:procurement": { path: "procurement/cases", plan: "business" },
  "ws:erp": { path: "erp/postings", plan: "business" },
  "ws:radar": { path: "anomalies", plan: "business" },
  "ws:process": { path: "process/overview", plan: "enterprise" },
  "ws:corroboration": { path: "corroboration/runs", plan: "enterprise" },
  "ws:assertion-reviews": { path: "assertions/clause-checks", plan: "enterprise" },
};

const ORG_PROBES: Record<string, { path: string; plan: Plan }> = {
  "org:autonomy": { path: "autonomy", plan: "enterprise" },
  "org:egress": { path: "egress", plan: "enterprise" },
};

const GATED_WS = WORKSPACE_PAGES.filter((p) => p.plan !== "any" && p.navId && NAV_LABEL[p.navId]);
const GATED_ORG = ORGANIZATION_PAGES.filter((p) => p.plan !== "any" && p.navId !== "org:analytics");
const ORG_NAV_LABEL: Record<string, string> = {
  "org:transactional-email": "Transactional email",
  "org:developer": "Developer platform",
  "org:branding": "Branding & custom domains",
  "org:autonomy": "Calibrated autonomy",
  "org:egress": "Egress lockdown",
  "org:api-keys": "API keys",
  "org:webhooks": "Webhooks",
  "org:identity": "Enterprise identity",
  // N-021: bring your own AI key is Business and Enterprise.
  "org:byok": "Enterprise BYOK & models",
};

const TENANT_OWNER: Record<"A" | "B" | "C", UserKey> = { A: "A.owner", B: "B.owner", C: "C.owner" };

for (const tenant of ["A", "B", "C"] as const) {
  const plan = TENANTS[tenant].plan as Plan;

  test.describe(`plan matrix — Tenant ${tenant} (${plan})`, () => {
    test.use({ user: TENANT_OWNER[tenant] });

    for (const target of GATED_WS) {
      const label = NAV_LABEL[target.navId as string] as string;
      const unlocked = RANK[plan] >= RANK[target.plan];

      test(`${label} is ${unlocked ? "unlocked" : "locked"} in the sidebar and on its page`, async ({ page, problems }) => {
        await page.goto(ws(tenant, "notifications"));
        await settle(page);
        const sidebar = page.getByRole("complementary").or(page.locator("aside")).first();
        if (unlocked) {
          await expect(sidebar.getByRole("link", { name: label, exact: true })).toBeVisible();
          await expect(page.getByRole("button", { name: `${label} (not included in your plan)` })).toHaveCount(0);
          await sidebar.getByRole("link", { name: label, exact: true }).click();
          await expect(page.locator("body")).toContainText(target.title);
          await expect(page.locator("main")).not.toContainText(LOCK_TEXT);
        } else {
          const locked = page.getByRole("button", { name: `${label} (not included in your plan)` });
          await expect(locked).toBeVisible();
          await locked.click();
          const dialog = page.getByRole("alertdialog");
          await expect(dialog).toContainText(`${label} isn't included in your plan`);
          await dialog.getByRole("button", { name: "View plans" }).click();
          await expect(page).toHaveURL(new RegExp(`/organizations/${TENANTS[tenant].org}/billing`));
          // Direct URL: a lock card, no data, no crash. A locked page's own
          // reads are refused by the server with 402; that is the gate working.
          problems.allowHttp(/\/api\/v1\/workspaces\//, [402], "server-side plan gate on a locked page");
          await page.goto(ws(tenant, target.sub));
          await settle(page);
          await expect(page.locator("main")).toContainText(LOCK_TEXT);
        }
        await expectHealthyPage(page);
      });
    }

    for (const target of GATED_ORG) {
      const label = ORG_NAV_LABEL[target.navId] as string;
      const unlocked = RANK[plan] >= RANK[target.plan];
      test(`organization ${label} is ${unlocked ? "unlocked" : "locked"}`, async ({ page, problems }) => {
        await page.goto(org(tenant, "settings"));
        await settle(page);
        if (unlocked) {
          await expect(page.getByRole("link", { name: label, exact: true })).toBeVisible();
          await page.getByRole("link", { name: label, exact: true }).click();
          await expect(page.locator("body")).toContainText(target.title);
          await settle(page);
          await expect(page.locator("main")).not.toContainText(/isn.t included in your plan|included on the/i);
        } else {
          const locked = page.getByRole("button", { name: `${label} (not included in your plan)` });
          await expect(locked).toBeVisible();
          await locked.click();
          const dialog = page.getByRole("alertdialog");
          await expect(dialog).toContainText(`${label} isn't included in your plan`);
          await dialog.getByRole("button", { name: "View plans" }).click();
          await expect(page).toHaveURL(new RegExp(`/organizations/${TENANTS[tenant].org}/billing`));
          problems.allowHttp(/\/api\/v1\/organizations\//, [402], "server-side plan gate on a locked page");
          await page.goto(org(tenant, target.sub));
          await settle(page);
          await expect(page.locator("main")).toContainText(LOCK_TEXT);
        }
        await expectHealthyPage(page);
      });
    }
  });

  test.describe(`plan matrix API — Tenant ${tenant} (${plan})`, () => {
    test("every gated feature answers 402 below its plan and 200 at or above it", async () => {
      const session = await loginAs(TENANT_OWNER[tenant]);
      const { workspaceId, organizationId } = await resolveWorkspaceId(
        session,
        TENANTS[tenant].org,
        TENANTS[tenant].ws,
      );
      const results: string[] = [];
      for (const [navId, probe] of Object.entries(WS_PROBES)) {
        const expected = RANK[plan] >= RANK[probe.plan] ? 200 : 402;
        const response = await api(session, "GET", `/workspaces/${workspaceId}/${probe.path}`);
        results.push(`${navId} GET ${probe.path} -> ${response.status} (expected ${expected})`);
        expect.soft(response.status, `${navId} ${probe.path}`).toBe(expected);
      }
      for (const [navId, probe] of Object.entries(ORG_PROBES)) {
        const expected = RANK[plan] >= RANK[probe.plan] ? 200 : 402;
        const response = await api(session, "GET", `/organizations/${organizationId}/${probe.path}`);
        results.push(`${navId} GET ${probe.path} -> ${response.status} (expected ${expected})`);
        expect.soft(response.status, `${navId} ${probe.path}`).toBe(expected);
      }
      test.info().annotations.push({ type: "api-probes", description: results.join("; ") });
    });
  });
}

test.describe("plan matrix — Enterprise has zero locks", () => {
  test.use({ user: "C.owner" });
  test("no lock icon anywhere in the workspace or organization sidebar", async ({ page }) => {
    await page.goto(ws("C"));
    await settle(page, 1000);
    await expect(page.getByTestId("nav-lock")).toHaveCount(0);
    await expect(page.getByTestId("nav-locked-row")).toHaveCount(0);
    await page.goto(org("C", "settings"));
    await settle(page, 1000);
    await expect(page.getByTestId("nav-lock")).toHaveCount(0);
    await expect(page.getByTestId("nav-locked-row")).toHaveCount(0);
  });
});

// Keep the compiler honest about the tenant keys used above.
const _tenants: TenantKey[] = ["A", "B", "C"];
void _tenants;
