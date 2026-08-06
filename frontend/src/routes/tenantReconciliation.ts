/**
 * URL/state reconciliation for FlowPilot AI.
 *
 * Both the URL and the persisted store name a tenant, and they can disagree —
 * a deep link, a back button, a second browser tab.
 *
 * THE RULE: the URL wins whenever it names a tenant the actor can reach.
 *
 * A user who opens /beta/main/work-items is stating which workspace they want.
 * The store is a memory of where they were last, which is a weaker signal than
 * an explicit navigation. The store is then updated to match, so it remains a
 * useful default rather than drifting away from reality.
 *
 * When the URL names a tenant the actor CANNOT reach, this returns
 * "unreachable" rather than substituting a valid one. Substitution would mean
 * a removed member lands in a different workspace and assumes nothing changed
 * — the same class of silent misdirection as the pre-ARCH-01 guard sending
 * expired sessions to the onboarding screen.
 *
 * Pure by design, and self-checked below. This function decides what a user
 * sees after a deep link, and a mistake here is invisible until someone
 * reports landing somewhere unexpected.
 */

import { workspacePath } from "@/routes/tenantPaths";
import type {
  OrganizationMembershipSummary,
  WorkspaceSummary,
} from "@/types/tenancy";

/** The subset of a ready TenantState that reconciliation needs. */
export interface ReadyTenant {
  organization: OrganizationMembershipSummary;
  workspace: WorkspaceSummary;
  organizations: OrganizationMembershipSummary[];
}

export type TenantReconciliation =
  /** URL and state agree, or the URL named a reachable tenant. Render it. */
  | {
      action: "render";
      organization: OrganizationMembershipSummary;
      workspace: WorkspaceSummary;
      /** True when the store should be updated to match the URL. */
      shouldSyncSelection: boolean;
    }
  /** The URL carries no tenant. Send the actor to the resolved one. */
  | { action: "redirect"; to: string }
  /**
   * The URL named a tenant the actor cannot reach.
   *
   * Deliberately NOT a silent substitution: the caller routes to the picker
   * so the actor learns their destination is gone rather than quietly
   * appearing somewhere else.
   */
  | { action: "unreachable"; reason: "organization" | "workspace" };

/**
 * Reconciles the tenant named in the URL against the resolved tenant state.
 *
 * @param tenant - The resolved ready state.
 * @param urlOrgSlug - Organization slug from the route, if any.
 * @param urlWorkspaceSlug - Workspace slug from the route, if any.
 */
export const reconcileTenantWithUrl = (
  tenant: ReadyTenant,
  urlOrgSlug: string | undefined,
  urlWorkspaceSlug: string | undefined,
): TenantReconciliation => {
  // No tenant in the URL — a bare /, or a legacy flat path. Send the actor to
  // the resolved tenant so the address bar becomes authoritative from here on.
  if (!urlOrgSlug || !urlWorkspaceSlug) {
    return {
      action: "redirect",
      to: workspacePath(tenant.organization.organization_slug, tenant.workspace.slug),
    };
  }

  const organization = tenant.organizations.find(
    (candidate) => candidate.organization_slug === urlOrgSlug,
  );

  if (!organization) {
    return { action: "unreachable", reason: "organization" };
  }

  const workspace = organization.workspaces.find(
    (candidate) => candidate.slug === urlWorkspaceSlug,
  );

  if (!workspace) {
    return { action: "unreachable", reason: "workspace" };
  }

  const alreadySelected =
    organization.organization_id === tenant.organization.organization_id &&
    workspace.id === tenant.workspace.id;

  return {
    action: "render",
    organization,
    workspace,
    shouldSyncSelection: !alreadySelected,
  };
};

/* ==========================================================================
 * Development self-check
 * ========================================================================== */

const ws = (slug: string, orgId: string): WorkspaceSummary => ({
  id: `${orgId}-${slug}`,
  organization_id: orgId,
  slug,
  workspace_name: slug,
  status: "ACTIVE",
  effective_role: "ADMIN",
  company_logo_url: null,
});

const org = (
  slug: string,
  workspaces: WorkspaceSummary[],
): OrganizationMembershipSummary => ({
  organization_id: slug,
  organization_slug: slug,
  organization_name: slug,
  organization_status: "ACTIVE",
  role: "OWNER",
  workspaces,
});

/**
 * Runs every reconciliation assertion.
 *
 * @returns Failure descriptions. Empty means reconciliation is intact.
 */
export const runTenantReconciliationSelfCheck = (): string[] => {
  const failures: string[] = [];

  const expect = (label: string, condition: boolean): void => {
    if (!condition) {
      failures.push(label);
    }
  };

  const acmeEng = ws("engineering", "acme");
  const acmeSales = ws("sales", "acme");
  const acme = org("acme", [acmeEng, acmeSales]);

  const betaMain = ws("main", "beta");
  const beta = org("beta", [betaMain]);

  const tenant: ReadyTenant = {
    organization: acme,
    workspace: acmeEng,
    organizations: [acme, beta],
  };

  /* --- No tenant in the URL ---------------------------------------------- */
  const bare = reconcileTenantWithUrl(tenant, undefined, undefined);
  expect(
    "a URL with no tenant redirects to the resolved workspace",
    bare.action === "redirect" && bare.to === "/acme/engineering",
  );

  /* --- URL agrees with state --------------------------------------------- */
  const agreeing = reconcileTenantWithUrl(tenant, "acme", "engineering");
  expect(
    "a URL matching the resolved tenant renders",
    agreeing.action === "render",
  );
  expect(
    "no store write when URL and state already agree",
    agreeing.action === "render" && agreeing.shouldSyncSelection === false,
  );

  /* --- THE CORE RULE: the URL wins --------------------------------------- */
  const deepLink = reconcileTenantWithUrl(tenant, "beta", "main");
  expect(
    "A DEEP LINK TO ANOTHER REACHABLE TENANT WINS OVER THE STORE",
    deepLink.action === "render" &&
      deepLink.organization.organization_slug === "beta" &&
      deepLink.workspace.slug === "main",
  );
  expect(
    "the store is synced when the URL overrides it",
    deepLink.action === "render" && deepLink.shouldSyncSelection === true,
  );

  const siblingWorkspace = reconcileTenantWithUrl(tenant, "acme", "sales");
  expect(
    "a sibling workspace in the same organization is honoured",
    siblingWorkspace.action === "render" &&
      siblingWorkspace.workspace.slug === "sales",
  );

  /* --- Unreachable tenants are NOT silently substituted ------------------ */
  const foreignOrg = reconcileTenantWithUrl(tenant, "gamma", "main");
  expect(
    "an unreachable organization reports unreachable, not a substitute",
    foreignOrg.action === "unreachable" && foreignOrg.reason === "organization",
  );

  const foreignWorkspace = reconcileTenantWithUrl(tenant, "acme", "archived");
  expect(
    "an unreachable workspace reports unreachable, not a substitute",
    foreignWorkspace.action === "unreachable" &&
      foreignWorkspace.reason === "workspace",
  );
  expect(
    "an unreachable workspace does NOT fall back to a sibling",
    foreignWorkspace.action !== "render",
  );

  /* --- Partial URL params ------------------------------------------------ */
  expect(
    "an organization slug without a workspace slug redirects",
    reconcileTenantWithUrl(tenant, "acme", undefined).action === "redirect",
  );

  return failures;
};

/** Runs the self-check and reports failures to the console. */
export const assertTenantReconciliationIntegrity = (): void => {
  const failures = runTenantReconciliationSelfCheck();

  if (failures.length === 0) {
    // eslint-disable-next-line no-console
    console.info("[routes] tenant reconciliation self-check passed");
    return;
  }

  // eslint-disable-next-line no-console
  console.error(
    `[routes] TENANT RECONCILIATION SELF-CHECK FAILED — ${failures.length} case(s):\n  - ` +
      failures.join("\n  - "),
  );
};
