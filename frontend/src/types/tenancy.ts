/**
 * Tenancy type contract for FlowPilot AI.
 *
 * Mirrors the ARCH-01 backend schemas exactly. Every type here has a
 * counterpart in app/schemas/organization.py, workspace.py, workspace_member.py,
 * or me.py, and field names match the wire format (snake_case) rather than
 * being camelised — so a response can be used directly without a mapping layer
 * that would need updating on both sides of every future change.
 *
 * Two structural facts from the backend are load-bearing here:
 *
 *   1. WorkspaceRole has no OWNER. A workspace does not own itself; the
 *      organization owns it. Ownership lives on OrganizationRole.
 *
 *   2. A workspace member response may carry is_derived = true with id = null.
 *      That is an organization OWNER or ADMIN whose workspace ADMIN role is
 *      computed rather than stored, so there is no membership row to reference
 *      and no workspace-level grant to revoke.
 */

/* ==========================================================================
 * Enumerations
 * ========================================================================== */

/**
 * Organization-level roles. Govern the commercial and identity surface.
 *
 * Deliberately NOT a ladder. BILLING grants billing visibility while granting
 * less content access than MEMBER, so no ordering exists that would make a
 * "minimum role" comparison correct. Membership tests use explicit sets.
 */
export type OrganizationRole = "OWNER" | "ADMIN" | "BILLING" | "MEMBER";

/**
 * Workspace-level access grants. These DO form a true ladder:
 * ADMIN > CONTRIBUTOR > VIEWER.
 */
export type WorkspaceRole = "ADMIN" | "CONTRIBUTOR" | "VIEWER";

/**
 * Membership lifecycle, shared by organization seats and workspace grants.
 *
 * Replaces the pre-ARCH-01 is_active boolean, which could not distinguish
 * "invited but not yet accepted" from "removed by an administrator" from
 * "temporarily suspended". DEACTIVATED is terminal; the row is retained for
 * attribution rather than deleted.
 */
export type MembershipStatus =
  | "INVITED"
  | "ACTIVE"
  | "SUSPENDED"
  | "DEACTIVATED";

/** Commercial tenant lifecycle. SUSPENDED is reversible; ARCHIVED is a soft delete. */
export type OrganizationStatus = "ACTIVE" | "SUSPENDED" | "ARCHIVED";

/** Collaboration boundary lifecycle. ARCHIVED is a soft delete. */
export type WorkspaceStatus = "ACTIVE" | "ARCHIVED" | "SUSPENDED";

/** Invitation state machine. */
export type InvitationStatus =
  | "PENDING"
  | "ACCEPTED"
  | "REJECTED"
  | "EXPIRED"
  | "REVOKED";

/* ==========================================================================
 * Shared projections
 * ========================================================================== */

/**
 * Minimal user projection embedded in membership responses.
 *
 * Mirrors the User model, which carries no display-name column. Do not add
 * full_name here: the backend cannot populate it.
 */
export interface UserSummary {
  id: string;
  email: string;
  is_active: boolean;
}

/* ==========================================================================
 * Organization
 * ========================================================================== */

export interface Organization {
  id: string;
  slug: string;
  name: string;
  legal_name: string | null;
  status: OrganizationStatus;
  created_at: string;
  updated_at: string;
}

/** A seat in an organization. The billable unit. */
export interface OrganizationMember {
  id: string;
  organization_id: string;
  user: UserSummary;
  role: OrganizationRole;
  status: MembershipStatus;
  deactivated_at: string | null;
  created_at: string;
}

export interface OrganizationMemberList {
  items: OrganizationMember[];
  total: number;
  /** Includes pending invitations, which reserve a seat. */
  seats_consumed: number;
}

export interface OrganizationCreateRequest {
  organization_name: string;
  workspace_name?: string;
  organization_slug?: string;
  legal_name?: string;
  timezone?: string;
  language?: string;
  currency?: string;
  date_format?: string;
}

export interface OrganizationUpdateRequest {
  name?: string;
  legal_name?: string;
  slug?: string;
}

export interface OrganizationMemberRoleUpdateRequest {
  role: OrganizationRole;
}

export interface OwnershipTransferRequest {
  target_membership_id: string;
}

export interface SlugAvailability {
  slug: string;
  available: boolean;
  reason: string | null;
}

/* ==========================================================================
 * Workspace
 * ========================================================================== */

export interface Workspace {
  id: string;
  organization_id: string;
  slug: string;
  workspace_name: string;
  status: WorkspaceStatus;
  timezone: string;
  language: string;
  currency: string;
  date_format: string;
  company_logo_url: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * Compact workspace projection for switchers and bootstrap payloads.
 *
 * Carries the caller's effective role so permission-dependent affordances can
 * be rendered without a request per workspace.
 */
export interface WorkspaceSummary {
  id: string;
  organization_id: string;
  slug: string;
  workspace_name: string;
  status: WorkspaceStatus;
  company_logo_url: string | null;
  effective_role: WorkspaceRole;
}

export interface WorkspaceCreateRequest {
  workspace_name: string;
  slug?: string;
  timezone?: string;
  language?: string;
  currency?: string;
  date_format?: string;
}

export interface WorkspaceUpdateRequest {
  workspace_name?: string;
  slug?: string;
  timezone?: string;
  language?: string;
  currency?: string;
  date_format?: string;
  company_logo_url?: string;
}

/**
 * A workspace access grant.
 *
 * `id` is null and `is_derived` is true for an organization OWNER or ADMIN
 * whose ADMIN role is computed rather than stored. The UI must disable
 * revocation for those entries: there is no workspace-level grant to revoke,
 * because the access follows from the organization role.
 */
export interface WorkspaceMember {
  id: string | null;
  workspace_id: string;
  user: UserSummary;
  role: WorkspaceRole;
  status: MembershipStatus;
  is_derived: boolean;
  organization_role: OrganizationRole | null;
  created_at: string | null;
}

export interface WorkspaceMemberList {
  items: WorkspaceMember[];
  total: number;
}

export interface WorkspaceMemberGrantRequest {
  user_id: string;
  role: WorkspaceRole;
}

export interface WorkspaceMemberRoleUpdateRequest {
  role: WorkspaceRole;
}

/* ==========================================================================
 * Bootstrap context
 * ========================================================================== */

/** The authenticated actor. No display-name column exists on the backend. */
export interface MeUser {
  id: string;
  email: string;
  is_active: boolean;
}

export interface OrganizationMembershipSummary {
  organization_id: string;
  organization_slug: string;
  organization_name: string;
  organization_status: OrganizationStatus;
  role: OrganizationRole;
  workspaces: WorkspaceSummary[];
}

/**
 * Everything the client needs on boot, in one round trip.
 *
 * The three states this response distinguishes are what the pre-ARCH-01
 * frontend could not tell apart. OnboardingGuard treated any falsy workspace
 * result as "no workspace", so an expired token and a genuinely
 * membership-less user produced the same signal — and session expiry sent
 * people to the workspace creation screen instead of the login page.
 *
 *   401 response                     -> token invalid or expired -> /login
 *   200 with requires_onboarding     -> authenticated, no tenant -> onboarding
 *   200 with organizations populated -> normal                   -> workspace
 *
 * requires_onboarding is reachable only with a valid session, so it can never
 * be confused with an authentication failure.
 */
export interface MeContext {
  user: MeUser;
  organizations: OrganizationMembershipSummary[];
  default_organization_id: string | null;
  default_workspace_id: string | null;
  requires_onboarding: boolean;
}

/* ==========================================================================
 * Invitations
 * ========================================================================== */

export interface WorkspaceInvitation {
  id: string;
  workspace_id: string;
  organization_id: string | null;
  inviter_id: string;
  email: string;
  role: WorkspaceRole;
  status: InvitationStatus;
  expires_at: string;
  accepted_at: string | null;
  rejected_at: string | null;
  revoked_at: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * Public preview of an invitation, resolved from its token.
 *
 * Served without authentication so a recipient can see what they are being
 * asked to join before creating an account. Carries no database identifiers.
 */
export interface WorkspaceInvitationPreview {
  organization_name: string;
  workspace_name: string;
  inviter_email: string;
  invited_email: string;
  role: WorkspaceRole;
  expires_at: string;
}

/**
 * Result of accepting an invitation.
 *
 * Carries the destination so the client can navigate straight into the
 * workspace without a follow-up bootstrap call.
 */
export interface WorkspaceInvitationAccepted {
  invitation: WorkspaceInvitation;
  organization_id: string;
  organization_slug: string;
  workspace_id: string;
  workspace_slug: string;
  workspace_role: WorkspaceRole;
}

export interface WorkspaceInvitationCreateRequest {
  email: string;
  role: WorkspaceRole;
}

export interface InvitationTokenRequest {
  token: string;
}
