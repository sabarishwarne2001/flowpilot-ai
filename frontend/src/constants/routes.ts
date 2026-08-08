/**
 * Global route constants for FlowPilot AI.
 *
 * Only paths with no tenant in them belong here. Tenant-scoped paths are built
 * by @/routes/tenantPaths, because they depend on runtime slugs and cannot be
 * expressed as constants.
 *
 * MIGRATION STATE
 *
 * The LEGACY block below holds the flat, pre-ARCH-01 paths. They are retained
 * so that Sidebar, navigation.ts, DashboardLayout, and every page continue to
 * compile while the route tree is migrated. They are removed in Step 8, once
 * their consumers read from tenantPaths instead.
 *
 * Retaining them is not indecision: deleting them now would break twenty files
 * with no replacement wired in, and the application would be no more correct
 * for it.
 */

export const ROUTES = {
  /* ======================================================================
   * Unauthenticated
   * ====================================================================== */

  LOGIN: "/login",
  REGISTER: "/register",

  /**
   * Email verification landing page.
   *
   * Public. The token arrives in the URL fragment (ARCH-03 §B.9), so the page
   * is reachable signed out — which is the common case, since the link opens
   * from a mail client in whatever browser is default.
   */
  VERIFY_EMAIL: "/verify-email",

  /* ======================================================================
   * Authenticated, tenant-independent
   * ====================================================================== */

  /**
   * Tenant creation.
   *
   * Reachable ONLY when /me/context reports requires_onboarding. An expired
   * session routes to LOGIN, never here — conflating the two is the defect
   * ARCH-01 removed.
   */
  ONBOARDING: "/onboarding",

  /** Tenant picker, for actors in more than one organization or workspace. */
  WORKSPACES: "/workspaces",

  /**
   * Tombstone for removed, suspended, or archived tenants.
   *
   * Distinct from a permission error: the actor is told what happened rather
   * than shown a generic denial.
   */
  NO_ACCESS: "/no-access",

  /** Token-addressed. Preview is public; accept and reject require a session. */
  INVITATION_ACCEPT: "/invitations/accept",

  /* ======================================================================
   * LEGACY — flat, pre-ARCH-01. Removed in Step 8.
   * ====================================================================== */

  DASHBOARD: "/",
  WORK_ITEMS: "/work-items",
  WORK_ITEM_DETAILS: "/work-items/:id",

  ASSISTANT: "/assistant",
  AUTOMATION: "/automation",

  NOTIFICATIONS: "/notifications",

  PROFILE: "/profile",

  SETTINGS: "/settings",
  ACCOUNT: "/account",

  /* ====================================================================== */

  NOT_FOUND: "*",
} as const;

export type RouteValue = (typeof ROUTES)[keyof typeof ROUTES];
