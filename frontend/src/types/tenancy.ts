/**
 * Tenancy type contract for FlowPilot AI.
 */

export type OrganizationRole = "OWNER" | "ADMIN" | "BILLING" | "MEMBER";
export type WorkspaceRole = "ADMIN" | "CONTRIBUTOR" | "VIEWER";
export type MembershipStatus = "INVITED" | "ACTIVE" | "SUSPENDED" | "DEACTIVATED";
export type OrganizationStatus = "ACTIVE" | "SUSPENDED" | "ARCHIVED";
export type WorkspaceStatus = "ACTIVE" | "ARCHIVED" | "SUSPENDED";
export type InvitationStatus = "PENDING" | "ACCEPTED" | "REJECTED" | "EXPIRED" | "REVOKED";

export interface UserSummary {
  id: string;
  email: string;
  is_active: boolean;
  /** Set in Settings -> Profile; null until the person chooses one (show the email then). */
  display_name?: string | null;
  /** F-153: whether a profile picture is set, so lists request only real avatars. */
  has_avatar?: boolean;
}

export interface Organization {
  id: string;
  slug: string;
  name: string;
  legal_name: string | null;
  status: OrganizationStatus;
  created_at: string;
  updated_at: string;
}

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

export interface SlugAvailability {
  slug: string;
  available: boolean;
  reason: string | null;
}

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

export interface WorkspaceSummary {
  id: string;
  organization_id: string;
  slug: string;
  workspace_name: string;
  status: WorkspaceStatus;
  company_logo_url: string | null;
  effective_role: WorkspaceRole;
}

/**
 * One explicit workspace grant (`/me/workspaces`), with its organization (F-142).
 * `archived` is true when the workspace or its organization is archived.
 */
export interface WorkspaceGrantSummary extends WorkspaceSummary {
  organization_name: string;
  organization_slug: string;
  organization_status: OrganizationStatus;
  archived: boolean;
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
}

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

export interface MeUser {
  id: string;
  email: string;
  is_active: boolean;
  /** Set in Settings -> Profile; null until the user chooses one. */
  display_name?: string | null;
}

export interface OrganizationMembershipSummary {
  organization_id: string;
  organization_slug: string;
  organization_name: string;
  organization_status: OrganizationStatus;
  role: OrganizationRole;
  workspaces: WorkspaceSummary[];
}

export interface MeContext {
  user: MeUser;
  organizations: OrganizationMembershipSummary[];
  default_organization_id: string | null;
  default_workspace_id: string | null;
  requires_onboarding: boolean;
}

export interface WorkspaceInvitation {
  id: string;
  workspace_id: string;
  organization_id: string | null;
  inviter_id: string;
  email: string;
  role: WorkspaceRole;
  /** The organization role the invitation grants (what the pending lists show). */
  organization_role?: OrganizationRole;
  status: InvitationStatus;
  expires_at: string;
  accepted_at: string | null;
  rejected_at: string | null;
  revoked_at: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * The public preview of an invitation (POST /invitations/preview).
 * Mirrors OrganizationInvitationPreviewResponse in app/schemas/organization_invitation.py.
 * F-222: this described a single workspace and role the server never sent;
 * `has_account` says whether the invited address can sign in already.
 */
export interface WorkspaceInvitationPreview {
  organization_name: string;
  inviter_email: string;
  invited_email: string;
  organization_role: OrganizationRole;
  workspaces: { name: string; role: WorkspaceRole }[];
  expires_at: string;
  /** Null when the invitation went through the organization's own mail server (F-226). */
  has_account: boolean | null;
  /** N-035. The organization requires single sign-on: the invitee joins through it, never with a password. */
  sso_required: boolean;
}

/** POST /auth/register/invitation: the new account's session, and where the invitation leads. */
export interface InvitationSignupResponse {
  access_token: string;
  token_type: string;
  organization_slug: string;
  workspace_slug: string | null;
}

/**
 * Result of accepting an organization invitation (POST /invitations/accept).
 * Mirrors OrganizationInvitationAcceptResponse in app/schemas/organization_invitation.py.
 * F-107: `workspace_slug` is the first workspace granted, or null when none was.
 */
export interface OrganizationInvitationAccepted {
  invitation_id: string;
  organization_id: string;
  organization_slug: string;
  organization_role: string;
  provisioned_grants: ReadonlyArray<{ workspace_name: string; role: string }>;
  skipped_grant_count: number;
  workspace_slug: string | null;
}

/**
 * One workspace-and-role pair provisioned when the invitation is accepted.
 * Mirrors WorkspaceGrantInput in app/schemas/organization_invitation.py.
 */
export interface WorkspaceGrantInput {
  workspace_id: string;
  role: WorkspaceRole;
}

/**
 * Input to issue an organization invitation.
 */
export interface WorkspaceInvitationCreateRequest {
  email: string;
  organization_role: OrganizationRole;
  grants: WorkspaceGrantInput[];
}

export interface InvitationTokenRequest {
  token: string;
}
