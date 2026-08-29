/**
 * Tenant URL grammar for FlowPilot AI.
 */

export const ROUTE_PARAMS = {
  orgSlug: "orgSlug",
  workspaceSlug: "workspaceSlug",
  workItemId: "id",
} as const;

export interface TenantRouteParams {
  orgSlug?: string;
  workspaceSlug?: string;
}

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

const P_ORG = `:${ROUTE_PARAMS.orgSlug}`;
const P_WS = `:${ROUTE_PARAMS.workspaceSlug}`;
const P_ITEM = `:${ROUTE_PARAMS.workItemId}`;

export const ROUTE_PATTERNS = {
  organizationShell: `/organizations/${P_ORG}`,
  organizationSettings: "settings",
  organizationMembers: "members",
  organizationApiKeys: "api-keys",
  organizationWebhooks: "webhooks",
  organizationBilling: "billing",
  organizationBillingReturn: "billing/return",
  organizationIdentity: "identity",
  organizationAudit: "audit",
  organizationNewWorkspace: `/organizations/${P_ORG}/workspaces/new`,

  workspaceShell: `/${P_ORG}/${P_WS}`,
  workspaceDashboard: "",
  workspaceWorkItems: "work-items",
  workspaceWorkItemDetails: `work-items/${P_ITEM}`,
  workspaceAssistant: "assistant",
  workspaceAutomation: "automation",
  workspaceAutomationTimeline: "automation/timeline",
  workspaceVerification: "verification",
  workspaceNotifications: "notifications",
  workspaceSettings: "settings",
} as const;

const seg = (value: string): string => encodeURIComponent(value);

export const organizationPath = (orgSlug: string): string =>
  `/organizations/${seg(orgSlug)}`;

export const organizationSettingsPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/settings`;

export const organizationMembersPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/members`;

export const organizationApiKeysPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/api-keys`;

export const organizationWebhooksPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/webhooks`;

export const organizationBillingPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/billing`;

export const organizationBillingReturnPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/billing/return`;

export const organizationIdentityPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/identity`;

export const organizationAuditPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/audit`;

export const createWorkspacePath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/workspaces/new`;

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

export const automationTimelinePath = (
  orgSlug: string,
  workspaceSlug: string,
): string => `${automationPath(orgSlug, workspaceSlug)}/timeline`;

export const verificationPath = (
  orgSlug: string,
  workspaceSlug: string,
): string => `${workspacePath(orgSlug, workspaceSlug)}/verification`;

export const notificationsPath = (
  orgSlug: string,
  workspaceSlug: string,
): string => `${workspacePath(orgSlug, workspaceSlug)}/notifications`;

export const workspaceSettingsPath = (
  orgSlug: string,
  workspaceSlug: string,
): string => `${workspacePath(orgSlug, workspaceSlug)}/settings`;

export interface ParsedTenantPath {
  orgSlug: string;
  workspaceSlug: string;
  rest: string;
}

export const isReservedSegment = (segment: string): boolean =>
  RESERVED_ROUTE_SEGMENTS.has(segment.toLowerCase());

export const parseTenantPath = (pathname: string): ParsedTenantPath | null => {
  if (!pathname.startsWith("/")) {
    return null;
  }

  const segments = pathname.split("/").filter(Boolean);
  if (segments.length < 2) {
    return null;
  }

  const [first, second, ...rest] = segments;
  if (!first || !second || isReservedSegment(first)) {
    return null;
  }

  let orgSlug: string;
  let workspaceSlug: string;

  try {
    orgSlug = decodeURIComponent(first);
    workspaceSlug = decodeURIComponent(second);
  } catch {
    return null;
  }

  return {
    orgSlug,
    workspaceSlug,
    rest: rest.join("/"),
  };
};

export const isTenantPath = (pathname: string): boolean =>
  parseTenantPath(pathname) !== null;

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

export const toTenantPath = (
  pathname: string,
  orgSlug: string,
  workspaceSlug: string,
): string => {
  const root = workspacePath(orgSlug, workspaceSlug);
  const parsed = parseTenantPath(pathname);
  const rest = parsed ? parsed.rest : pathname.replace(/^\/+/, "");
  return rest ? `${root}/${rest}` : root;
};

export const isSafeRedirectPath = (path: string | null | undefined): boolean => {
  if (!path) {
    return false;
  }
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(path)) {
    return false;
  }
  if (!path.startsWith("/")) {
    return false;
  }
  if (path.startsWith("//") || path.startsWith("/\\")) {
    return false;
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(path)) {
    return false;
  }
  return true;
};

export const loginPathWithRedirect = (destination: string): string => {
  if (!isSafeRedirectPath(destination)) {
    return "/login";
  }
  return `/login?redirect=${encodeURIComponent(destination)}`;
};

export const runTenantPathSelfCheck = (): string[] => {
  const failures: string[] = [];
  const expect = (label: string, condition: boolean): void => {
    if (!condition) {
      failures.push(label);
    }
  };

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

  const parsed = parseTenantPath("/acme/engineering/work-items/abc");
  expect(
    "a tenant path parses into org, workspace, and remainder",
    parsed?.orgSlug === "acme" &&
      parsed?.workspaceSlug === "engineering" &&
      parsed?.rest === "work-items/abc",
  );

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
    "switching tenant keeps the current sub-page",
    rebaseTenantPath("/acme/engineering/work-items", "beta", "main") ===
      "/beta/main/work-items",
  );
  expect(
    "rebasing a non-tenant path falls back to the workspace root",
    rebaseTenantPath("/login", "beta", "main") === "/beta/main",
  );

  expect(
    "A LEGACY FLAT PATH KEEPS ITS SUB-PAGE WHEN FORWARDED",
    toTenantPath("/work-items", "acme", "engineering") ===
      "/acme/engineering/work-items",
  );

  expect(
    "A TENANT DEEP LINK IS A VALID REDIRECT",
    isSafeRedirectPath("/acme/engineering/work-items/abc"),
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
