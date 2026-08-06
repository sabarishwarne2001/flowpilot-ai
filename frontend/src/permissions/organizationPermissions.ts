/**
 * Organization-level authorization logic for the FlowPilot AI frontend.
 *
 * A faithful mirror of app/core/organization_permissions.py. Every function
 * here has a counterpart there with the same name and the same rule.
 *
 * SCOPE: these functions decide what the UI SHOWS. The server decides what is
 * ALLOWED. Their only job is preventing a user from clicking something that
 * will return 403 — RequireOrgRole enforces the real boundary on every
 * request. If this file and the backend disagree, the backend wins and the
 * user sees an error; the failure mode is a confusing interface, never an
 * authorization bypass.
 *
 * That is also why a partial mirror is worse than none: an approximate copy
 * produces buttons that fail. Any change here must be made in
 * organization_permissions.py in the same commit, and vice versa.
 *
 * Two concepts are kept deliberately separate, exactly as on the backend:
 *
 *   PRECEDENCE  administrative ordering, used ONLY to decide whether an actor
 *               may act upon another member's role. Carries no capability
 *               implication.
 *   CAPABILITY  explicit predicates, one per action. Never derived from
 *               precedence.
 *
 * BILLING is why the separation matters. It grants billing visibility while
 * granting less content access than MEMBER, so it occupies no coherent
 * position on a single ordinal ladder. It sits at the SAME precedence as
 * MEMBER — neither can administer anyone — and receives its billing capability
 * explicitly.
 */

import type { OrganizationRole } from "@/types/tenancy";

/* ==========================================================================
 * Precedence
 * ========================================================================== */

/**
 * Administrative precedence. Used ONLY by the canModify* and canAssign*
 * functions to determine whether an actor outranks a target.
 *
 * BILLING and MEMBER are peers at weight 1. Neither can administer the other,
 * and neither can administer anyone else, so no ordering between them exists
 * or is needed. Do not read a capability implication into these numbers.
 */
export const ORGANIZATION_ROLE_PRECEDENCE: Readonly
  Record<OrganizationRole, number>
> = {
  OWNER: 3,
  ADMIN: 2,
  BILLING: 1,
  MEMBER: 1,
};

/** Roles permitted to administer an organization's members and workspaces. */
export const ADMINISTRATIVE_ROLES: ReadonlySet<OrganizationRole> = new Set
  OrganizationRole
>(["OWNER", "ADMIN"]);

/**
 * Roles that receive an implicit ADMIN grant on every workspace in the
 * organization. Consumed by resolveEffectiveWorkspaceRole.
 */
export const IMPLICIT_WORKSPACE_ADMIN_ROLES: ReadonlySet<OrganizationRole> =
  ADMINISTRATIVE_ROLES;

/**
 * Roles a non-OWNER administrator may assign. Promotion to ADMIN or OWNER is
 * reserved to OWNER, so an administrator cannot manufacture a peer and thereby
 * escape the strict precedence check in canModifyMember.
 */
export const ADMIN_ASSIGNABLE_ROLES: ReadonlySet<OrganizationRole> = new Set
  OrganizationRole
>(["MEMBER", "BILLING"]);

export const precedence = (role: OrganizationRole): number =>
  ORGANIZATION_ROLE_PRECEDENCE[role];

/**
 * True if the actor's precedence strictly exceeds the target's.
 *
 * Strict comparison is deliberate: peers may not act on one another. Without
 * it, one administrator could demote another and an organization could be
 * captured by whichever admin acted first.
 */
export const outranks = (
  actorRole: OrganizationRole,
  targetRole: OrganizationRole,
): boolean => precedence(actorRole) > precedence(targetRole);

/* ==========================================================================
 * Billing
 * ========================================================================== */

/**
 * Whether the role may view invoices, plan, and usage.
 *
 * BILLING exists precisely for this: in mid-market and enterprise deals the
 * person holding the payment method is typically a finance controller who must
 * never see customer documents.
 */
export const canViewBilling = (role: OrganizationRole): boolean =>
  role === "OWNER" || role === "ADMIN" || role === "BILLING";

/**
 * Whether the role may change the plan, payment method, or cancel.
 *
 * OWNER only. Changing the plan alters the contract, and contract authority is
 * not delegable to an operational administrator.
 */
export const canManageBilling = (role: OrganizationRole): boolean =>
  role === "OWNER";

/**
 * Whether the role may purchase or release seats.
 *
 * Available to ADMIN because seat changes are an operational consequence of
 * hiring, not a contractual decision. BILLING is excluded: it may observe
 * spend, not cause it.
 */
export const canManageSeats = (role: OrganizationRole): boolean =>
  ADMINISTRATIVE_ROLES.has(role);

/* ==========================================================================
 * Organization
 * ========================================================================== */

export const canManageOrganizationSettings = (
  role: OrganizationRole,
): boolean => ADMINISTRATIVE_ROLES.has(role);

/** Archiving or deleting the entire tenant. OWNER only. */
export const canDeleteOrganization = (role: OrganizationRole): boolean =>
  role === "OWNER";

export const canTransferOwnership = (role: OrganizationRole): boolean =>
  role === "OWNER";

/**
 * SSO, domain capture, and SCIM configuration. OWNER only.
 *
 * Identity configuration can redirect authentication for every member of the
 * tenant, which makes it an ownership-level capability regardless of how
 * operational it appears. Consumed from ARCH-09.
 */
export const canConfigureSso = (role: OrganizationRole): boolean =>
  role === "OWNER";

/** 2FA enforcement, session TTL, IP allowlists. OWNER only. From ARCH-09. */
export const canManageSecurityPolicy = (role: OrganizationRole): boolean =>
  role === "OWNER";

/** Reading the organization audit trail. From ARCH-07. */
export const canViewAuditLog = (role: OrganizationRole): boolean =>
  ADMINISTRATIVE_ROLES.has(role);

/** Creating or revoking API keys. From ARCH-08. */
export const canManageApiKeys = (role: OrganizationRole): boolean =>
  ADMINISTRATIVE_ROLES.has(role);

/** Configuring webhook endpoints. From ARCH-08. */
export const canManageWebhooks = (role: OrganizationRole): boolean =>
  ADMINISTRATIVE_ROLES.has(role);

/* ==========================================================================
 * Workspace provisioning
 * ========================================================================== */

/**
 * Whether the role may create a new workspace inside this organization.
 *
 * Distinct from creating a new ORGANIZATION, which is an account-level
 * capability available to any authenticated user and governed by no role at
 * all. A Viewer in Acme may found their own organization; that says nothing
 * about their standing in Acme. There is deliberately no canCreateOrganization
 * function — it would imply a permission that does not exist.
 */
export const canCreateWorkspace = (role: OrganizationRole): boolean =>
  ADMINISTRATIVE_ROLES.has(role);

/**
 * Whether the role may archive or delete a workspace.
 *
 * Held at organization level rather than workspace level: a workspace does not
 * own itself, so its destruction is a decision for the tenant that does.
 */
export const canDeleteWorkspace = (role: OrganizationRole): boolean =>
  ADMINISTRATIVE_ROLES.has(role);

/* ==========================================================================
 * Member administration
 * ========================================================================== */

export const canManageMembers = (role: OrganizationRole): boolean =>
  ADMINISTRATIVE_ROLES.has(role);

export const canInviteMembers = (role: OrganizationRole): boolean =>
  ADMINISTRATIVE_ROLES.has(role);

/**
 * Whether the actor may assign the given role, at invitation or promotion.
 *
 * This is the check whose absence allowed a Manager to invite at OWNER level
 * before ARCH-01. It applies at BOTH invitation creation and role change: an
 * invitation is a deferred role assignment, and enforcing only on promotion
 * leaves the escalation path open.
 *
 *   - OWNER may assign any role, including OWNER (ownership transfer).
 *   - ADMIN may assign only MEMBER and BILLING.
 *   - No other role may assign anything.
 */
export const canAssignOrganizationRole = (
  actorRole: OrganizationRole,
  targetRole: OrganizationRole,
): boolean => {
  if (!canManageMembers(actorRole)) {
    return false;
  }
  if (actorRole === "OWNER") {
    return true;
  }
  return ADMIN_ASSIGNABLE_ROLES.has(targetRole);
};

/**
 * Whether the actor may act on an existing member holding the target role.
 *
 * Covers deactivation, suspension, and role change. Requires administrative
 * standing plus strictly greater precedence, so:
 *
 *   - OWNER may act on ADMIN, BILLING, and MEMBER, but not another OWNER.
 *   - ADMIN may act on BILLING and MEMBER, but not an OWNER or another ADMIN.
 *   - BILLING and MEMBER may act on no one.
 *
 * An OWNER acting on another OWNER is disallowed here and handled by the
 * explicit ownership transfer flow, which enforces the accompanying
 * invariants. Self-directed actions (leaving, self-demotion) are not covered
 * by this function.
 */
export const canModifyMember = (
  actorRole: OrganizationRole,
  targetRole: OrganizationRole,
): boolean => {
  if (!canManageMembers(actorRole)) {
    return false;
  }
  return outranks(actorRole, targetRole);
};

/**
 * Whether the actor may change a member from one role to another.
 *
 * Both halves are required: the actor must outrank the member as they stand
 * today, AND be permitted to assign the role they are moving to. Checking only
 * one half leaves an escalation path open in the other direction.
 */
export const canModifyMemberRole = (
  actorRole: OrganizationRole,
  targetCurrentRole: OrganizationRole,
  targetNewRole: OrganizationRole,
): boolean =>
  canModifyMember(actorRole, targetCurrentRole) &&
  canAssignOrganizationRole(actorRole, targetNewRole);
