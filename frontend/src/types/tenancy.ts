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

export interface OwnershipTransferRequest {
  target_membership_id: string;
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
  status: InvitationStatus;
  expires_at: string;
  accepted_at: string | null;
  rejected_at: string | null;
  revoked_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceInvitationPreview {
  organization_name: string;
  workspace_name: string;
  inviter_email: string;
  invited_email: string;
  role: WorkspaceRole;
  expires_at: string;
}

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
