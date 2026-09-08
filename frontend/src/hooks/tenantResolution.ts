/**
 * Pure tenant resolution for FlowPilot AI.
 */

import type {
  MeContext,
  MeUser,
  OrganizationMembershipSummary,
  OrganizationRole,
  WorkspaceRole,
  WorkspaceSummary,
} from "@/types/tenancy";

/* ==========================================================================
 * Selection
 * ========================================================================== */

export interface TenantSelection {
  activeOrganizationId: string | null;
  activeWorkspaceId: string | null;
  lastWorkspaceByOrganization: Readonly<Record<string, string>>;
}

export const EMPTY_SELECTION: TenantSelection = {
  activeOrganizationId: null,
  activeWorkspaceId: null,
  lastWorkspaceByOrganization: {},
};

export interface TenantRouteLocator {
  orgSlug?: string;
  workspaceSlug?: string;
}

export const NO_ROUTE_LOCATOR: TenantRouteLocator = {};

/* ==========================================================================
 * State
 * ========================================================================== */

export type TenantStatus =
  | "loading"
  | "unauthenticated"
  | "error"
  | "onboarding_required"
  | "no_workspace"
  | "ready";

export type TenantState =
  | { status: "loading" }
  | { status: "unauthenticated" }
  | { status: "error"; error: unknown }
  | { status: "onboarding_required"; user: MeUser }
  | {
      status: "no_workspace";
      user: MeUser;
      organization: OrganizationMembershipSummary;
      organizations: OrganizationMembershipSummary[];
    }
  | {
      status: "ready";
      user: MeUser;
      organization: OrganizationMembershipSummary;
      workspace: WorkspaceSummary;
      organizationRole: OrganizationRole;
      workspaceRole: WorkspaceRole;
      organizations: OrganizationMembershipSummary[];
    };

/* ==========================================================================
 * Resolution
 * ========================================================================== */

export const resolveOrganization = (
  context: MeContext,
  selection: TenantSelection,
  route: TenantRouteLocator = NO_ROUTE_LOCATOR,
): OrganizationMembershipSummary | null => {
  const organizations = context.organizations;

  if (organizations.length === 0) {
    return null;
  }

  const byId = (id: string | null): OrganizationMembershipSummary | undefined =>
    id ? organizations.find((o) => o.organization_id === id) : undefined;

  const bySlug = (
    slug: string | undefined,
  ): OrganizationMembershipSummary | undefined =>
    slug ? organizations.find((o) => o.organization_slug === slug) : undefined;

  return (
    bySlug(route.orgSlug) ??
    byId(selection.activeOrganizationId) ??
    byId(context.default_organization_id) ??
    organizations[0] ??
    null
  );
};

export const resolveWorkspace = (
  context: MeContext,
  selection: TenantSelection,
  organization: OrganizationMembershipSummary,
  route: TenantRouteLocator = NO_ROUTE_LOCATOR,
): WorkspaceSummary | null => {
  const workspaces = organization.workspaces;

  if (workspaces.length === 0) {
    return null;
  }

  const byId = (id: string | null | undefined): WorkspaceSummary | undefined =>
    id ? workspaces.find((w) => w.id === id) : undefined;

  const bySlug = (slug: string | undefined): WorkspaceSummary | undefined =>
    slug ? workspaces.find((w) => w.slug === slug) : undefined;

  const remembered =
    selection.lastWorkspaceByOrganization[organization.organization_id];

  return (
    bySlug(route.workspaceSlug) ??
    byId(remembered) ??
    byId(selection.activeWorkspaceId) ??
    byId(context.default_workspace_id) ??
    workspaces[0] ??
    null
  );
};

export interface ResolveTenantInput {
  isAuthenticated: boolean;
  isLoading: boolean;
  isUnauthorized: boolean;
  error: unknown;
  context: MeContext | undefined;
  selection: TenantSelection;
  route?: TenantRouteLocator;
}

export const resolveTenant = (input: ResolveTenantInput): TenantState => {
  const {
    isAuthenticated,
    isLoading,
    isUnauthorized,
    error,
    context,
    selection,
    route = NO_ROUTE_LOCATOR,
  } = input;

  if (!isAuthenticated) {
    return { status: "unauthenticated" };
  }

  if (isUnauthorized) {
    return { status: "unauthenticated" };
  }

  if (isLoading) {
    return { status: "loading" };
  }

  if (error) {
    return { status: "error", error };
  }

  if (!context) {
    return { status: "loading" };
  }

  if (context.requires_onboarding || context.organizations.length === 0) {
    return { status: "onboarding_required", user: context.user };
  }

  const organization = resolveOrganization(context, selection, route);

  if (!organization) {
    return { status: "onboarding_required", user: context.user };
  }

  const workspace = resolveWorkspace(context, selection, organization, route);

  if (!workspace) {
    return {
      status: "no_workspace",
      user: context.user,
      organization,
      organizations: context.organizations,
    };
  }

  return {
    status: "ready",
    user: context.user,
    organization,
    workspace,
    organizationRole: organization.role,
    workspaceRole: workspace.effective_role,
    organizations: context.organizations,
  };
};

/* ==========================================================================
 * Development self-check
 * ========================================================================== */

const stubUser: MeUser = {
  id: "user-1",
  email: "founder@acme.test",
  is_active: true,
};

const stubWorkspace = (
  id: string,
  organizationId: string,
  role: WorkspaceRole = "ADMIN",
): WorkspaceSummary => ({
  id,
  organization_id: organizationId,
  slug: id,
  workspace_name: id,
  status: "ACTIVE",
  effective_role: role,
  company_logo_url: null,
});

const stubOrganization = (
  id: string,
  workspaces: WorkspaceSummary[],
  role: OrganizationRole = "OWNER",
): OrganizationMembershipSummary => ({
  organization_id: id,
  organization_slug: id,
  organization_name: id,
  organization_status: "ACTIVE",
  role,
  workspaces,
});

const stubContext = (
  organizations: OrganizationMembershipSummary[],
  requiresOnboarding = false,
): MeContext => ({
  user: stubUser,
  organizations,
  default_organization_id: organizations[0]?.organization_id ?? null,
  default_workspace_id: organizations[0]?.workspaces[0]?.id ?? null,
  requires_onboarding: requiresOnboarding,
});

const baseInput = (
  context: MeContext | undefined,
  selection: TenantSelection = EMPTY_SELECTION,
): ResolveTenantInput => ({
  isAuthenticated: true,
  isLoading: false,
  isUnauthorized: false,
  error: undefined,
  context,
  selection,
});

export const runTenantResolutionSelfCheck = (): string[] => {
  const failures: string[] = [];

  const expect = (label: string, condition: boolean): void => {
    if (!condition) {
      failures.push(label);
    }
  };

  const wsA1 = stubWorkspace("acme-eng", "acme");
  const wsA2 = stubWorkspace("acme-sales", "acme", "VIEWER");
  const orgA = stubOrganization("acme", [wsA1, wsA2]);

  const wsB1 = stubWorkspace("beta-main", "beta");
  const orgB = stubOrganization("beta", [wsB1], "MEMBER");

  expect(
    "no session resolves to unauthenticated",
    resolveTenant({ ...baseInput(undefined), isAuthenticated: false }).status ===
      "unauthenticated",
  );
  expect(
    "EXPIRED TOKEN resolves to unauthenticated, NOT onboarding",
    resolveTenant({ ...baseInput(undefined), isUnauthorized: true }).status ===
      "unauthenticated",
  );
  expect(
    "401 wins over loading — no routing decision from a rejected session",
    resolveTenant({
      ...baseInput(undefined),
      isUnauthorized: true,
      isLoading: true,
    }).status === "unauthenticated",
  );
  expect(
    "in-flight bootstrap resolves to loading, never a redirect",
    resolveTenant({ ...baseInput(undefined), isLoading: true }).status ===
      "loading",
  );
  expect(
    "a non-auth failure resolves to error, not onboarding",
    resolveTenant({ ...baseInput(undefined), error: new Error("boom") })
      .status === "error",
  );

  expect(
    "requires_onboarding resolves to onboarding_required",
    resolveTenant(baseInput(stubContext([], true))).status ===
      "onboarding_required",
  );
  expect(
    "an empty organization list resolves to onboarding_required",
    resolveTenant(baseInput(stubContext([]))).status === "onboarding_required",
  );

  const ready = resolveTenant(baseInput(stubContext([orgA])));
  expect("a single tenant resolves to ready", ready.status === "ready");

  const chosen = resolveTenant(
    baseInput(stubContext([orgA, orgB]), {
      activeOrganizationId: "beta",
      activeWorkspaceId: null,
      lastWorkspaceByOrganization: {},
    }),
  );
  expect(
    "a valid persisted organization overrides the server default",
    chosen.status === "ready" && chosen.organization.organization_id === "beta",
  );

  const remembered = resolveTenant(
    baseInput(stubContext([orgA]), {
      activeOrganizationId: "acme",
      activeWorkspaceId: null,
      lastWorkspaceByOrganization: { acme: "acme-sales" },
    }),
  );
  expect(
    "per-organization memory wins over the server default workspace",
    remembered.status === "ready" && remembered.workspace.id === "acme-sales",
  );

  const emptyDefault = stubOrganization("flowpilot-dev", [], "MEMBER");
  const acmeWs = stubWorkspace("general", "acme");
  const acmeOrg = stubOrganization("acme", [acmeWs]);
  const twoOrgs = stubContext([emptyDefault, acmeOrg]);

  expect(
    "an empty DEFAULT organization with no URL still resolves to no_workspace",
    resolveTenant(baseInput(twoOrgs)).status === "no_workspace",
  );

  const urlDriven = resolveTenant({
    ...baseInput(twoOrgs),
    route: { orgSlug: "acme", workspaceSlug: "general" },
  });
  expect(
    "THE FIX: /acme/general resolves to ready even when the default org is empty",
    urlDriven.status === "ready" &&
      urlDriven.organization.organization_id === "acme" &&
      urlDriven.workspace.id === "general",
  );

  const urlOrgOnly = resolveTenant({
    ...baseInput(twoOrgs),
    route: { orgSlug: "acme" },
  });
  expect(
    "an org slug alone is enough to escape the empty default organization",
    urlOrgOnly.status === "ready" &&
      urlOrgOnly.organization.organization_id === "acme",
  );

  const foreignSlug = resolveTenant({
    ...baseInput(twoOrgs),
    route: { orgSlug: "an-org-this-actor-cannot-reach" },
  });
  expect(
    "an unreachable org slug falls back rather than reporting onboarding",
    foreignSlug.status === "no_workspace" &&
      foreignSlug.organization.organization_id === "flowpilot-dev",
  );

  const foreignWorkspaceSlug = resolveTenant({
    ...baseInput(twoOrgs),
    route: { orgSlug: "acme", workspaceSlug: "a-workspace-that-is-not-here" },
  });
  expect(
    "an unreachable workspace slug does NOT leak across organizations",
    foreignWorkspaceSlug.status === "ready" &&
      foreignWorkspaceSlug.organization.organization_id === "acme",
  );

  expect(
    "no route locator leaves selection-based resolution untouched",
    resolveTenant(
      baseInput(twoOrgs, {
        activeOrganizationId: "acme",
        activeWorkspaceId: null,
        lastWorkspaceByOrganization: {},
      }),
    ).status === "ready",
  );

  const empty = resolveTenant(
    baseInput(stubContext([stubOrganization("gamma", [], "MEMBER")])),
  );
  expect(
    "an organization with no reachable workspace resolves to no_workspace",
    empty.status === "no_workspace",
  );
  expect(
    "no_workspace is NOT onboarding_required — the actor already has a tenant",
    empty.status !== "onboarding_required",
  );

  return failures;
};

/** Runs the self-check and reports failures to the console. */
export const assertTenantResolutionIntegrity = (): void => {
  const failures = runTenantResolutionSelfCheck();

  if (failures.length === 0) {
    console.info("[tenant] resolution self-check passed");
    return;
  }

  console.error(
    `[tenant] RESOLUTION SELF-CHECK FAILED — ${failures.length} case(s):\n  - ` +
      failures.join("\n  - "),
  );
};
