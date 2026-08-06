/**
 * Workspace-level authorization logic for the FlowPilot AI frontend.
 *
 * A faithful mirror of app/core/workspace_permissions.py.
 *
 * The central function is resolveEffectiveWorkspaceRole. Organization OWNER
 * and ADMIN hold an implicit ADMIN grant on every workspace in their
 * organization, DERIVED rather than stored. The server computes it per request
 * so an organization role change takes effect immediately; the client computes
 * it the same way from the bootstrap context.
 *
 * This is what the pre-ARCH-01 inline checks could not express. They read
 * `myMembership?.role`, which is null for an organization admin holding no
 * stored workspace grant — so the most privileged users saw the fewest
 * controls.
 *
 * WorkspaceRole has no OWNER. A workspace does not own itself; the
 * organization owns it.
 *
 * SCOPE: affordance only. RequireWorkspaceRole is the real boundary.
 */

import type { OrganizationRole, WorkspaceRole } from "@/types/tenancy";
import { IMPLICIT_WORKSPACE_ADMIN_ROLES } from "@/permissions/organizationPermissions";

/* ==========================================================================
 * Precedence
 * ========================================================================== */

/**
 * Workspace access ordering.
 *
 * Unlike the organization tier, this IS a true ladder: every capability of a
 * lower role is held by a higher one. That is why isAtLeast is safe here and
 * deliberately absent from the organization module, where BILLING breaks the
 * property.
 */
export const WORKSPACE_ROLE_PRECEDENCE: Readonly
  Record<WorkspaceRole, number>
> = {
  ADMIN: 3,
  CONTRIBUTOR: 2,
  VIEWER: 1,
};

/**
 * Roles a workspace ADMIN may grant directly. Granting or revoking workspace
 * ADMIN is reserved to organization administrators; see canAssignWorkspaceRole.
 */
export const WORKSPACE_ADMIN_ASSIGNABLE_ROLES: ReadonlySet<WorkspaceRole> =
  new Set<WorkspaceRole>(["CONTRIBUTOR", "VIEWER"]);

export const precedence = (role: WorkspaceRole): number =>
  WORKSPACE_ROLE_PRECEDENCE[role];

/** True if the role meets or exceeds the minimum required role. */
export const isAtLeast = (
  role: WorkspaceRole,
  minimum: WorkspaceRole,
): boolean => precedence(role) >= precedence(minimum);

/* ==========================================================================
 * Effective role resolution
 * ========================================================================== */

/**
 * Resolves the role an actor effectively holds in a workspace.
 *
 * The single authority on workspace access in the UI. Every role-dependent
 * affordance consumes its result.
 *
 * Resolution:
 *   1. No active organization membership -> null. Organization membership is a
 *      precondition for any workspace access; a workspace grant without one is
 *      an invariant violation, not an access path.
 *   2. Organization OWNER or ADMIN -> "ADMIN", derived, whether or not an
 *      explicit grant exists.
 *   3. Otherwise -> the explicit grant, which may be null.
 *
 * BILLING and MEMBER both fall through to case 3. A finance controller holding
 * BILLING with no workspace grant resolves to null and sees no workspace
 * affordances at all, which is the purpose of the role.
 *
 * @param organizationRole - The actor's role in the workspace's parent
 *   organization, or null if they hold no active membership.
 * @param workspaceRole - The actor's explicit workspace grant, or null.
 */
export const resolveEffectiveWorkspaceRole = (
  organizationRole: OrganizationRole | null | undefined,
  workspaceRole: WorkspaceRole | null | undefined,
): WorkspaceRole | null => {
  if (!organizationRole) {
    return null;
  }

  if (IMPLICIT_WORKSPACE_ADMIN_ROLES.has(organizationRole)) {
    return "ADMIN";
  }

  return workspaceRole ?? null;
};

/**
 * Whether the actor has any access at all to the workspace.
 *
 * A false result corresponds to a server 404, not 403: acknowledging that a
 * workspace exists to someone with no access is an enumeration oracle.
 */
export const hasWorkspaceAccess = (
  organizationRole: OrganizationRole | null | undefined,
  workspaceRole: WorkspaceRole | null | undefined,
): boolean =>
  resolveEffectiveWorkspaceRole(organizationRole, workspaceRole) !== null;

/* ==========================================================================
 * Content
 * ========================================================================== */

export const canViewContent = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "VIEWER");

export const canCreateContent = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "CONTRIBUTOR");

export const canEditOwnContent = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "CONTRIBUTOR");

/**
 * Editing or deleting content created by other members.
 *
 * ADMIN only. Contributors own their own work; overriding a colleague's
 * document is an administrative act.
 */
export const canEditAnyContent = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "ADMIN");

/**
 * Querying the AI assistant.
 *
 * CONTRIBUTOR and above. Assistant queries consume metered capacity and create
 * conversation records, which makes them a write-shaped action even though the
 * user experiences them as reading.
 */
export const canUseAssistant = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "CONTRIBUTOR");

/**
 * Creating or modifying automation rules.
 *
 * ADMIN only. An automation rule acts on behalf of the whole workspace and
 * executes without further review, so authoring one is equivalent to
 * delegating a standing permission.
 */
export const canManageAutomation = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "ADMIN");

/**
 * Bulk-exporting workspace data.
 *
 * ADMIN only. Bulk egress is the action most worth constraining in a document
 * platform, and it is distinct from reading individual documents. ARCH-06
 * makes this a per-workspace capability toggle.
 */
export const canExportData = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "ADMIN");

/* ==========================================================================
 * Workspace administration
 * ========================================================================== */

export const canManageWorkspaceSettings = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "ADMIN");

export const canManageWorkspaceMembers = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "ADMIN");

/**
 * Inviting a user into this workspace.
 *
 * ADMIN only for now. ARCH-06 introduces a per-workspace toggle allowing
 * contributors to invite, mirroring the equivalent setting in Slack, Notion,
 * and Linear.
 */
export const canInviteToWorkspace = (role: WorkspaceRole): boolean =>
  isAtLeast(role, "ADMIN");

/**
 * Whether the actor may grant the given workspace role.
 *
 * The signature spans both tiers because the decision genuinely does:
 *
 *   - Workspace ADMIN may grant CONTRIBUTOR and VIEWER.
 *   - Only organization OWNER or ADMIN may grant workspace ADMIN.
 *
 * Restricting ADMIN grants to the organization tier resolves a deadlock that a
 * single-tier rule cannot. If workspace administrators could neither create
 * nor modify a peer, two ADMINs in one workspace would be mutually
 * unmanageable with no higher workspace role to break the tie. Holding that
 * authority one level up mirrors GitHub, where repository admins manage
 * collaborators and organization owners manage repository admins.
 */
export const canAssignWorkspaceRole = (
  organizationRole: OrganizationRole | null | undefined,
  effectiveWorkspaceRole: WorkspaceRole | null | undefined,
  targetRole: WorkspaceRole,
): boolean => {
  if (!effectiveWorkspaceRole) {
    return false;
  }
  if (!canManageWorkspaceMembers(effectiveWorkspaceRole)) {
    return false;
  }

  if (targetRole === "ADMIN") {
    return (
      !!organizationRole && IMPLICIT_WORKSPACE_ADMIN_ROLES.has(organizationRole)
    );
  }

  return WORKSPACE_ADMIN_ASSIGNABLE_ROLES.has(targetRole);
};

/**
 * Whether the actor may modify or revoke an existing workspace grant.
 *
 * Symmetric with canAssignWorkspaceRole: revoking a workspace ADMIN requires
 * organization-level standing, while CONTRIBUTOR and VIEWER grants are
 * administrable from within the workspace.
 *
 * Note for the member-list UI: a derived grant (is_derived === true, id ===
 * null) has no workspace-level membership row to revoke. Revocation controls
 * must be disabled for those entries regardless of what this function returns,
 * because the access follows from the organization role and is changed there.
 */
export const canModifyWorkspaceMember = (
  organizationRole: OrganizationRole | null | undefined,
  effectiveWorkspaceRole: WorkspaceRole | null | undefined,
  targetRole: WorkspaceRole,
): boolean => {
  if (!effectiveWorkspaceRole) {
    return false;
  }
  if (!canManageWorkspaceMembers(effectiveWorkspaceRole)) {
    return false;
  }

  if (targetRole === "ADMIN") {
    return (
      !!organizationRole && IMPLICIT_WORKSPACE_ADMIN_ROLES.has(organizationRole)
    );
  }

  return true;
};
