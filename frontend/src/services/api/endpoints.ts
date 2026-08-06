/**
 * Centralized API path construction for FlowPilot AI.
 *
 * Every tenant-scoped URL in the application is built here. This exists
 * because ARCH-01 embeds a tenant identifier in nearly every path, and the
 * previous pattern — each service defining its own endpoint constants — would
 * scatter that requirement across a dozen modules. One forgotten identifier is
 * a 404 discovered by a user rather than by the compiler.
 *
 * Path shape follows the ARCH-01 contract:
 *
 *   /organizations/{organization_id}/...   organization-scoped
 *   /workspaces/{workspace_id}/...         workspace-scoped
 *   /me/...                                actor-scoped, no tenant
 *   /invitations/...                       token-addressed, no tenant
 *
 * Workspace routes are NOT nested under their organization. A workspace
 * identifier already determines its organization, so requiring both would
 * create two sources of truth and an inconsistency check on every request.
 *
 * IDENTIFIERS, NOT SLUGS. These paths take UUIDs. Slugs are the human-facing
 * address in the browser URL (/acme/engineering/...) and are resolved to
 * identifiers once, at the tenant context boundary. Passing a slug to any
 * function here is a bug the type system cannot catch — the parameter names
 * say organizationId and workspaceId for that reason.
 */

/**
 * Encodes a path segment.
 *
 * Every identifier passed here is a UUID today and needs no encoding, but the
 * cost is one function call and the alternative is a path-injection bug the
 * first time a non-UUID segment appears.
 */
const seg = (value: string): string => encodeURIComponent(value);

/* ==========================================================================
 * Actor-scoped
 * ========================================================================== */

export const ME_ENDPOINTS = {
  /** Bootstrap: identity, tenants, and default destination in one call. */
  context: "/me/context",
  organizations: "/me/organizations",
  workspaces: "/me/workspaces",
} as const;

/* ==========================================================================
 * Organizations
 * ========================================================================== */

export const ORGANIZATION_ENDPOINTS = {
  /** Provision a tenant. Account-level; governed by no role. */
  create: "/organizations",

  slugAvailable: "/organizations/slug-available",

  detail: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}`,

  archive: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/archive`,

  leave: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/leave`,

  transferOwnership: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/transfer-ownership`,

  workspaces: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/workspaces`,

  members: (organizationId: string): string =>
    `/organizations/${seg(organizationId)}/members`,

  member: (organizationId: string, membershipId: string): string =>
    `/organizations/${seg(organizationId)}/members/${seg(membershipId)}`,

  /**
   * Deactivate rather than delete. The membership row is retained with the
   * actor and timestamp recorded, so attribution for past work survives.
   */
  deactivateMember: (organizationId: string, membershipId: string): string =>
    `/organizations/${seg(organizationId)}/members/${seg(membershipId)}/deactivate`,
} as const;

/* ==========================================================================
 * Workspaces
 * ========================================================================== */

export const WORKSPACE_ENDPOINTS = {
  detail: (workspaceId: string): string => `/workspaces/${seg(workspaceId)}`,

  logo: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/logo`,

  slugAvailable: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/slug-available`,

  archive: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/archive`,

  restore: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/restore`,

  leave: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/leave`,

  members: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/members`,

  member: (workspaceId: string, membershipId: string): string =>
    `/workspaces/${seg(workspaceId)}/members/${seg(membershipId)}`,

  revokeMember: (workspaceId: string, membershipId: string): string =>
    `/workspaces/${seg(workspaceId)}/members/${seg(membershipId)}/revoke`,
} as const;

/* ==========================================================================
 * Invitations
 * ========================================================================== */

export const INVITATION_ENDPOINTS = {
  /** Management, workspace-scoped. */
  list: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/invitations`,

  create: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/invitations`,

  revoke: (workspaceId: string, invitationId: string): string =>
    `/workspaces/${seg(workspaceId)}/invitations/${seg(invitationId)}/revoke`,

  resend: (workspaceId: string, invitationId: string): string =>
    `/workspaces/${seg(workspaceId)}/invitations/${seg(invitationId)}/resend`,

  /**
   * Token-addressed. Flat rather than workspace-scoped because a recipient has
   * no tenant context yet — establishing it is what the token is for.
   *
   * preview is public. accept and reject require authentication: the token
   * identifies the invitation, the session identifies the actor. Before
   * ARCH-01 both took only a token, so any holder of a forwarded link could
   * act on the invitee's behalf.
   */
  preview: "/invitations/preview",
  accept: "/invitations/accept",
  reject: "/invitations/reject",
} as const;
