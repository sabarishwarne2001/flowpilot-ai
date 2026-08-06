/**
 * Tenant URL grammar for FlowPilot AI.
 *
 * Every tenant-scoped path in the application is built here. Nothing
 * concatenates a slug into a URL by hand — the same reasoning as
 * services/api/endpoints.ts, one layer up: scattered construction means
 * scattered opportunities to forget a segment, and a forgotten segment is a
 * broken link found by a user rather than the compiler.
 *
 * THE SHAPE
 *
 *   /                                     public and global
 *   /login, /register                     unauthenticated
 *   /onboarding                           authenticated, no tenant
 *   /workspaces                           tenant picker
 *   /invitations/accept?token=            token-addressed
 *   /organizations/:orgSlug/...           organization-scoped
 *   /:orgSlug/:workspaceSlug/...          workspace-scoped
 *
 * WHY ORGANIZATION ROUTES ARE NAMESPACED
 *
 * Organization pages must be reachable when the actor has no workspace, so
 * they cannot nest under /:orgSlug/:workspaceSlug/. And /:orgSlug/settings —
 * one dynamic segment plus one static — is ambiguous against any future
 * top-level route. Placing them under the reserved "organizations" segment
 * removes the ambiguity by construction. GitHub draws the same line:
 * /orgs/acme/settings versus /acme/repo.
 *
 * WHY TWO BARE DYNAMIC SEGMENTS ARE SAFE
 *
 * /:orgSlug/:workspaceSlug could in principle swallow /work-items/abc. It
 * cannot, because the backend's reserved-slug list (app/core/slugs.py) refuses
 * every application route name as a tenant slug. RESERVED_ROUTE_SEGMENTS below
 * mirrors the subset that matters for routing, and the self-check asserts the
 * mirror holds.
 *
 * SLUGS HERE, IDENTIFIERS IN THE API
 *
 * These paths carry human-readable slugs. services/api/endpoints.ts carries
 * UUIDs. The two are bridged once, by the tenant context, which resolves slug
 * to identifier at the boundary. Never pass a UUID to a function in this file
 * or a slug to one in endpoints.ts.
 */

/* ==========================================================================
 * Route parameters
 * ========================================================================== */

/**
 * Route parameter names, declared once.
 *
 * useParams() reads these keys, and a typo there is a silent undefined rather
 * than a compile error — so the names live in one place and both the pattern
 * and the reader import them.
 */
export const ROUTE_PARAMS = {
  orgSlug: "orgSlug",
  workspaceSlug: "workspaceSlug",
  workItemId: "id",
} as const;

/** Params present on any workspace-scoped route. */
export interface TenantRouteParams {
  orgSlug?: string;
  workspaceSlug?: string;
}

/* ==========================================================================
 * Reserved segments
 * ========================================================================== */

/**
 * First-path segments the application owns.
 *
 * Mirrors the routing-relevant subset of RESERVED_SLUGS in app/core/slugs.py.
 * The backend refuses these as organization slugs, which is what guarantees
 * /:orgSlug/:workspaceSlug can never shadow an application route.
 *
 * This list is defence in depth, not the primary control — the backend already
 * makes such a slug unassignable. It exists so isTenantPath can answer
 * correctly without a network round trip, and so the self-check can prove the
 * two agree.
 */
export const RESERVED_ROUTE_SEGMENTS: ReadonlySet<string> = new Set<string>([
  "account",
  "assistant",
  "auth",
  "automation",
  "invitations",
  "login",
  "logout",
  "me",
  "no-access",
  "notifications",
  "onboarding",
  "organizations",
  "profile",
  "register",
  "settings",
  "work-items",
  "workspaces",
]);

/* ==========================================================================
 * Route patterns
 * ========================================================================== */

const P_ORG = `:${ROUTE_PARAMS.orgSlug}`;
const P_WS = `:${ROUTE_PARAMS.workspaceSlug}`;
const P_ITEM = `:${ROUTE_PARAMS.workItemId}`;

/**
 * Patterns for <Route path=...>. Step 6 consumes these.
 *
 * Workspace children are relative, so the shell route declares the tenant
 * prefix once and its children never repeat it.
 */
export const ROUTE_PATTERNS = {
  organizationShell: `/organizations/${P_ORG}`,
  organizationSettings: "settings",
  organizationMembers: "members",
  organizationBilling: "billing",

  workspaceShell: `/${P_ORG}/${P_WS}`,
  workspaceDashboard: "",
  workspaceWorkItems: "work-items",
  workspaceWorkItemDetails: `work-items/${P_ITEM}`,
  workspaceAssistant: "assistant",
  workspaceAutomation: "automation",
  workspaceNotifications: "notifications",
  workspaceSettings: "settings",
} as const;

/* ==========================================================================
 * Builders
 * ========================================================================== */

/**
 * Encodes a slug for use in a path.
 *
 * Backend slugs are already restricted to [a-z0-9-], so encoding is a no-op
 * today. It costs one call and closes a path-injection hole the first time a
 * slug arrives from somewhere less disciplined.
 */
const seg = (value: string): string => encodeURIComponent(value);

/** Root of an organization-scoped area. */
export const organizationPath = (orgSlug: string): string =>
  `/organizations/${seg(orgSlug)}`;

export const organizationSettingsPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/settings`;

export const organizationMembersPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/members`;

export const organizationBillingPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/billing`;

/**
 * Root of a workspace. Also the dashboard.
 *
 * The dashboard has no path of its own: a workspace's landing page IS the
 * workspace. Giving it a /dashboard suffix would make /acme/engineering a URL
 * that exists but shows nothing.
 */
export const workspacePath = (orgSlug: string, workspaceSlug: string): string =>
  `/${seg(orgSlug)}/${seg(workspaceSlug)}`;

export const workspaceDashboardPath = workspacePath;

export const workItemsPath = (orgSlug: string, workspaceSlug: string): string =>
  `${workspacePath(orgSlug, workspaceSlug)}/work-items`;

export const workItemDetailsPath = (
  orgSlug: string,
  workspaceSlug: string,
  workItemId: string,
): string =>
  `${workItemsPath(orgSlug, workspaceSlug)}/${encodeURIComponent(workItemId)}`;

export const assistantPath = (orgSlug: string, workspaceSlug: string): string =>
  `${workspacePath(orgSlug, workspaceSlug)}/assistant`;

export const automationPath = (
  orgSlug: string,
  workspaceSlug: string,
): string => `${workspacePath(orgSlug, workspaceSlug)}/automation`;

export const notificationsPath = (
  orgSlug: string,
  workspaceSlug: string,
): string => `${workspacePath(orgSlug, workspaceSlug)}/notifications`;

export const workspaceSettingsPath = (
  orgSlug: string,
  workspaceSlug: string,
): string => `${workspacePath(orgSlug, workspaceSlug)}/settings`;

/* ==========================================================================
 * Parsing
 * ========================================================================== */

export interface ParsedTenantPath {
  orgSlug: string;
  workspaceSlug: string;
  /** Remainder after the tenant prefix, without a leading slash. */
  rest: string;
}

/**
 * Whether a path's first segment is owned by the application.
 *
 * A reserved first segment means the path is global, never tenant-scoped.
 */
export const isReservedSegment = (segment: string): boolean =>
  RESERVED_ROUTE_SEGMENTS.has(segment.toLowerCase());

/**
 * Extracts the tenant prefix from a path, or null if it carries none.
 *
 * Returns null for global paths, single-segment paths, and anything whose
 * first segment is reserved — so /work-items/abc is correctly identified as
 * global rather than parsed as organization "work-items".
 */
export const parseTenantPath = (pathname: string): ParsedTenantPath | null => {
  if (!pathname.startsWith("/")) {
    return null;
  }

  const segments = pathname.split("/").filter(Boolean);

  if (segments.length < 2) {
    return null;
  }

  const [first, second, ...rest] = segments;

  if (!first || !second) {
    return null;
  }

  if (isReservedSegment(first)) {
    return null;
  }

  let orgSlug: string;
  let workspaceSlug: string;

  try {
    orgSlug = decodeURIComponent(first);
    workspaceSlug = decodeURIComponent(second);
  } catch {
    // Malformed percent-encoding. Treat as unparseable rather than throwing
    // from a routing helper.
    return null;
  }

  return {
    orgSlug,
    workspaceSlug,
    rest: rest.join("/"),
  };
};

/** Whether a path is workspace-scoped. */
export const isTenantPath = (pathname: string): boolean =>
  parseTenantPath(pathname) !== null;

/**
 * Rebuilds a path against a different tenant, preserving the sub-page.
 *
 * This is what makes switching feel correct: a user on
 * /acme/engineering/work-items who switches to Beta lands on
 * /beta/main/work-items, not back at a dashboard. Consumed by Step 8's
 * switcher.
 *
 * Falls back to the workspace root for a non-tenant path, since there is no
 * sub-page to carry across.
 */
export const rebaseTenantPath = (
  pathname: string,
  orgSlug: string,
  workspaceSlug: string,
): string => {
  const parsed = parseTenantPath(pathname);
  const root = workspacePath(orgSlug, workspaceSlug);

  if (!parsed || !parsed.rest) {
    return root;
  }

  return `${root}/${parsed.rest}`;
};

/* ==========================================================================
 * Redirect safety
 * ========================================================================== */

/**
 * Whether a path is safe to redirect to after authentication.
 *
 * STRUCTURAL, NOT AN ALLOWLIST. The previous implementation in Login.tsx
 * checked the path against a fixed list of prefixes:
 *
 *   const allowedPrefixes = ["/", "/work-items", "/assistant", ...];
 *
 * That was correct while every route was a known string. It cannot work once a
 * path segment is a user-chosen slug: /acme/engineering/work-items matches no
 * prefix, so every deep-link redirect would silently fall back to the
 * dashboard and the user's destination would be discarded without a trace.
 *
 * The property that actually matters for open-redirect safety is "this is a
 * same-origin relative path", which is structural. Rejected:
 *
 *   //evil.com/path      protocol-relative — the browser treats it as absolute
 *   /\evil.com           backslash — some browsers normalise this to //
 *   https://evil.com     absolute
 *   javascript:...       scheme injection
 *   anything not starting with /
 *   control characters   \n, \r, \t can split headers or bypass naive filters
 */
export const isSafeRedirectPath = (path: string | null | undefined): boolean => {
  if (!path) {
    return false;
  }

  // Control characters anywhere are disqualifying.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(path)) {
    return false;
  }

  if (!path.startsWith("/")) {
    return false;
  }

  // Protocol-relative and backslash variants resolve off-origin.
  if (path.startsWith("//") || path.startsWith("/\\")) {
    return false;
  }

  // A scheme before the first slash means it was never relative.
  if (/^[a-z][a-z0-9+.-]*:/i.test(path)) {
    return false;
  }

  return true;
};

/**
 * Builds a login URL that preserves where the user was heading.
 *
 * Used by every guard that finds an unauthenticated actor. Without it, session
 * expiry on a deep link loses the destination — which is half of the defect
 * ARCH-01 set out to fix; the other half was landing on onboarding instead of
 * login.
 */
export const loginPathWithRedirect = (destination: string): string => {
  if (!isSafeRedirectPath(destination)) {
    return "/login";
  }
  return `/login?redirect=${encodeURIComponent(destination)}`;
};

/* ==========================================================================
 * Development self-check
 * ========================================================================== */

/**
 * Runs every path assertion.
 *
 * @returns Failure descriptions. Empty means the grammar is intact.
 */
export const runTenantPathSelfCheck = (): string[] => {
  const failures: string[] = [];

  const expect = (label: string, condition: boolean): void => {
    if (!condition) {
      failures.push(label);
    }
  };

  /* --- Builders ---------------------------------------------------------- */
  expect(
    "workspace root is /:orgSlug/:workspaceSlug",
    workspacePath("acme", "engineering") === "/acme/engineering",
  );
  expect(
    "the dashboard IS the workspace root",
    workspaceDashboardPath("acme", "engineering") === "/acme/engineering",
  );
  expect(
    "work item details nest under the workspace",
    workItemDetailsPath("acme", "engineering", "abc-123") ===
      "/acme/engineering/work-items/abc-123",
  );
  expect(
    "organization routes are namespaced under a reserved segment",
    organizationSettingsPath("acme") === "/organizations/acme/settings",
  );

  /* --- Parsing ----------------------------------------------------------- */
  const parsed = parseTenantPath("/acme/engineering/work-items/abc");
  expect(
    "a tenant path parses into org, workspace, and remainder",
    parsed?.orgSlug === "acme" &&
      parsed?.workspaceSlug === "engineering" &&
      parsed?.rest === "work-items/abc",
  );
  expect(
    "the workspace root parses with an empty remainder",
    parseTenantPath("/acme/engineering")?.rest === "",
  );

  /* --- Reserved segments: the collision that must not happen ------------- */
  expect(
    "a legacy flat route is NOT parsed as a tenant path",
    parseTenantPath("/work-items/abc-123") === null,
  );
  expect(
    "organization routes are NOT parsed as workspace routes",
    parseTenantPath("/organizations/acme/settings") === null,
  );
  expect(
    "the invitation route is NOT parsed as a tenant path",
    parseTenantPath("/invitations/accept") === null,
  );
  expect(
    "single-segment and root paths carry no tenant",
    parseTenantPath("/login") === null &&
      parseTenantPath("/") === null &&
      parseTenantPath("") === null,
  );
  expect(
    "reserved segments are matched case-insensitively",
    isReservedSegment("Work-Items") && isReservedSegment("SETTINGS"),
  );

  /* --- Rebasing preserves the sub-page ----------------------------------- */
  expect(
    "switching tenant keeps the current sub-page",
    rebaseTenantPath("/acme/engineering/work-items", "beta", "main") ===
      "/beta/main/work-items",
  );
  expect(
    "rebasing a non-tenant path falls back to the workspace root",
    rebaseTenantPath("/login", "beta", "main") === "/beta/main",
  );

  /* --- Redirect safety: the regression this step prevents ---------------- */
  expect(
    "A TENANT DEEP LINK IS A VALID REDIRECT (the prefix allowlist rejected it)",
    isSafeRedirectPath("/acme/engineering/work-items/abc"),
  );
  expect(
    "ordinary relative paths remain valid",
    isSafeRedirectPath("/") && isSafeRedirectPath("/settings?tab=ai"),
  );
  expect(
    "protocol-relative URLs are rejected",
    !isSafeRedirectPath("//evil.example.com/path"),
  );
  expect(
    "backslash-prefixed URLs are rejected",
    !isSafeRedirectPath("/\\evil.example.com"),
  );
  expect(
    "absolute URLs are rejected",
    !isSafeRedirectPath("https://evil.example.com") &&
      !isSafeRedirectPath("javascript:alert(1)"),
  );
  expect(
    "non-relative and empty paths are rejected",
    !isSafeRedirectPath("evil.example.com") &&
      !isSafeRedirectPath("") &&
      !isSafeRedirectPath(null),
  );
  expect(
    "control characters are rejected",
    !isSafeRedirectPath("/path\nSet-Cookie: x=1"),
  );

  /* --- Login redirect construction --------------------------------------- */
  expect(
    "a safe destination is preserved on the login URL",
    loginPathWithRedirect("/acme/engineering/work-items") ===
      `/login?redirect=${encodeURIComponent("/acme/engineering/work-items")}`,
  );
  expect(
    "an unsafe destination is dropped rather than propagated",
    loginPathWithRedirect("//evil.example.com") === "/login",
  );

  return failures;
};

/** Runs the self-check and reports failures to the console. */
export const assertTenantPathIntegrity = (): void => {
  const failures = runTenantPathSelfCheck();

  if (failures.length === 0) {
    // eslint-disable-next-line no-console
    console.info("[routes] tenant path self-check passed");
    return;
  }

  // eslint-disable-next-line no-console
  console.error(
    `[routes] TENANT PATH SELF-CHECK FAILED — ${failures.length} case(s):\n  - ` +
      failures.join("\n  - "),
  );
};
