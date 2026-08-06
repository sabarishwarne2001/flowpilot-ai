/**
 * Pure tenant resolution for FlowPilot AI.
 *
 * Given a bootstrap context and a persisted selection, decides which
 * organization and workspace the actor is operating in and what state the
 * application is in.
 *
 * No React, no store, no network. Kept pure for the same reason as the
 * permission mirror in @/permissions: this logic decides where users land, and
 * a silent mistake here routes people to the wrong place without anything
 * failing loudly. Purity makes it self-checkable.
 *
 * THE STATE MACHINE
 *
 * The pre-ARCH-01 guard collapsed four situations into one falsy value:
 *
 *   const { data: workspace } = useQuery({ queryFn: getWorkspace });
 *   // falsy -> redirect to /onboarding
 *
 * An expired token, a removed member, a suspended tenant, and a genuinely new
 * user were indistinguishable. Session expiry therefore sent people to "Create
 * My Workspace" instead of the login page, and removed members founded phantom
 * organizations.
 *
 * TenantState is a discriminated union so a consumer that forgets a case fails
 * to compile rather than falling through to the most destructive branch.
 *
 * SELECTION IS VALIDATED, NEVER TRUSTED
 *
 * A persisted identifier can name a tenant the actor no longer belongs to.
 * Every resolution below checks the selection against the freshly fetched
 * context and falls back when it no longer resolves — which is exactly the
 * removed-member case the old guard mishandled.
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

/**
 * The persisted tenant selection.
 *
 * Identifiers only. Persisting the organization or workspace objects
 * themselves would mean a stale name after a rename and, more seriously, a
 * stale role after a demotion — a permission bug with a long shelf life in
 * localStorage. Identifiers are inert without server data to resolve them.
 */
export interface TenantSelection {
  activeOrganizationId: string | null;
  activeWorkspaceId: string | null;
  /** Last workspace visited per organization, so switching returns you home. */
  lastWorkspaceByOrganization: Readonly<Record<string, string>>;
}

export const EMPTY_SELECTION: TenantSelection = {
  activeOrganizationId: null,
  activeWorkspaceId: null,
  lastWorkspaceByOrganization: {},
};

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
  /** Bootstrap in flight. Render a splash, never a decision. */
  | { status: "loading" }
  /** No session, or the server rejected it. Route to login. */
  | { status: "unauthenticated" }
  /** Bootstrap failed for a reason other than authentication. */
  | { status: "error"; error: unknown }
  /**
   * Authenticated and belongs to no organization. Route to tenant creation.
   *
   * Reachable only from a successful response, so it can never be confused
   * with an authentication failure — the distinction the old guard lacked.
   */
  | { status: "onboarding_required"; user: MeUser }
  /**
   * Belongs to an organization but can reach no workspace inside it.
   *
   * A real state, not an error: an organization MEMBER holding no workspace
   * grant, or a BILLING controller who is not meant to have one. Route to a
   * picker or an explanatory screen, never to organization creation.
   */
  | {
      status: "no_workspace";
      user: MeUser;
      organization: OrganizationMembershipSummary;
      organizations: OrganizationMembershipSummary[];
    }
  /** Fully resolved. */
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

/**
 * Picks the organization the actor is operating in.
 *
 * Order: persisted selection, then the server's default, then the first
 * available. Each candidate is verified to exist in the context before use.
 */
export const resolveOrganization = (
  context: MeContext,
  selection: TenantSelection,
): OrganizationMembershipSummary | null => {
  const organizations = context.organizations;

  if (organizations.length === 0) {
    return null;
  }

  const byId = (id: string | null): OrganizationMembershipSummary | undefined =>
    id ? organizations.find((o) => o.organization_id === id) : undefined;

  return (
    byId(selection.activeOrganizationId) ??
    byId(context.default_organization_id) ??
    organizations[0] ??
    null
  );
};

/**
 * Picks the workspace within a resolved organization.
 *
 * Order: the last workspace visited in THIS organization, then the globally
 * active workspace if it belongs here, then the server's default, then the
 * first available.
 *
 * The per-organization memory comes first deliberately. Without it, switching
 * from Acme to Beta and back would drop you on Beta's default rather than
 * where you were working — the behaviour every multi-tenant product with a
 * switcher gets right.
 */
export const resolveWorkspace = (
  context: MeContext,
  selection: TenantSelection,
  organization: OrganizationMembershipSummary,
): WorkspaceSummary | null => {
  const workspaces = organization.workspaces;

  if (workspaces.length === 0) {
    return null;
  }

  const byId = (id: string | null | undefined): WorkspaceSummary | undefined =>
    id ? workspaces.find((w) => w.id === id) : undefined;

  const remembered =
    selection.lastWorkspaceByOrganization[organization.organization_id];

  return (
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
}

/**
 * Resolves the complete tenant state.
 *
 * Order of checks is deliberate and is the whole point of the function:
 *
 *   1. No session at all -> unauthenticated. Cheapest check, and it prevents
 *      every downstream branch from running against a session that cannot
 *      exist.
 *   2. Server rejected the session (401) -> unauthenticated. Checked BEFORE
 *      loading and before any tenancy reasoning, because an expired token must
 *      never be mistaken for "this user has no workspace". This single
 *      ordering is the fix for the defect that sent expired sessions to the
 *      onboarding screen.
 *   3. In flight -> loading. Render a splash; make no routing decision.
 *   4. Other failure -> error. Do not guess at tenancy from a failed request.
 *   5. requires_onboarding -> onboarding_required.
 *   6. Otherwise resolve organization, then workspace.
 */
export const resolveTenant = (input: ResolveTenantInput): TenantState => {
  const { isAuthenticated, isLoading, isUnauthorized, error, context, selection } =
    input;

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

  const organization = resolveOrganization(context, selection);

  if (!organization) {
    return { status: "onboarding_required", user: context.user };
  }

  const workspace = resolveWorkspace(context, selection, organization);

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

/**
 * Runs every resolution assertion.
 *
 * @returns Failure descriptions. Empty means the state machine is intact.
 */
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

  /* --- The defect this step exists to fix ------------------------------- */
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

  /* --- Onboarding is reachable ONLY from a successful response ---------- */
  expect(
    "requires_onboarding resolves to onboarding_required",
    resolveTenant(baseInput(stubContext([], true))).status ===
      "onboarding_required",
  );
  expect(
    "an empty organization list resolves to onboarding_required",
    resolveTenant(baseInput(stubContext([]))).status === "onboarding_required",
  );

  /* --- Happy path -------------------------------------------------------- */
  const ready = resolveTenant(baseInput(stubContext([orgA])));
  expect("a single tenant resolves to ready", ready.status === "ready");
  expect(
    "ready carries the resolved organization and workspace",
    ready.status === "ready" &&
      ready.organization.organization_id === "acme" &&
      ready.workspace.id === "acme-eng",
  );
  expect(
    "ready surfaces both roles",
    ready.status === "ready" &&
      ready.organizationRole === "OWNER" &&
      ready.workspaceRole === "ADMIN",
  );

  /* --- Selection is honoured --------------------------------------------- */
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

  /* --- Stale selection self-heals ---------------------------------------- */
  const staleOrg = resolveTenant(
    baseInput(stubContext([orgA]), {
      activeOrganizationId: "org-the-user-was-removed-from",
      activeWorkspaceId: null,
      lastWorkspaceByOrganization: {},
    }),
  );
  expect(
    "a stale organization falls back instead of dead-ending",
    staleOrg.status === "ready" &&
      staleOrg.organization.organization_id === "acme",
  );

  const staleWs = resolveTenant(
    baseInput(stubContext([orgA]), {
      activeOrganizationId: "acme",
      activeWorkspaceId: "workspace-since-archived",
      lastWorkspaceByOrganization: { acme: "workspace-since-archived" },
    }),
  );
  expect(
    "a stale workspace falls back instead of dead-ending",
    staleWs.status === "ready" && staleWs.workspace.id === "acme-eng",
  );

  /* --- no_workspace is distinct from onboarding_required ----------------- */
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
    // eslint-disable-next-line no-console
    console.info("[tenant] resolution self-check passed");
    return;
  }

  // eslint-disable-next-line no-console
  console.error(
    `[tenant] RESOLUTION SELF-CHECK FAILED — ${failures.length} case(s):\n  - ` +
      failures.join("\n  - "),
  );
};
