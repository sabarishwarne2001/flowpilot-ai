export interface Workspace {
  id: string;
  user_id: string;

  workspace_name: string;
  company_name: string;
  company_logo_url: string | null;

  timezone:
    | "Asia/Kolkata"
    | "Asia/Dubai"
    | "Europe/London"
    | "Europe/Berlin"
    | "America/New_York"
    | "America/Chicago"
    | "America/Los_Angeles"
    | "Asia/Singapore"
    | "Australia/Sydney"
    | "UTC";

  language:
    | "en"
    | "hi"
    | "ta"
    | "ml"
    | "te"
    | "kn"
    | "ar"
    | "de"
    | "fr"
    | "es"
    | "ja"
    | "zh";

  currency:
    | "INR"
    | "USD"
    | "EUR"
    | "GBP"
    | "AED"
    | "SGD"
    | "AUD"
    | "CAD"
    | "JPY"
    | "CNY";

  date_format:
    | "DD-MM-YYYY"
    | "MM-DD-YYYY"
    | "YYYY-MM-DD"
    | "DD/MM/YYYY"
    | "MM/DD/YYYY"
    | "YYYY/MM/DD";

  is_active: boolean;

  created_at: string;
  updated_at: string;
}

export type WorkspaceCreate = Omit<
  Workspace,
  "id" | "user_id" | "created_at" | "updated_at"
>;

// ============================================================================
// Memberships & Invitations (Sprint 2 Extensions)
// ============================================================================

export type WorkspaceRole = "OWNER" | "MANAGER" | "CONTRIBUTOR" | "VIEWER";

export type InvitationStatus = "PENDING" | "ACCEPTED" | "REJECTED" | "EXPIRED" | "REVOKED";

export interface WorkspaceUser {
  id: string;
  email: string;
  is_active: boolean;
}

export interface WorkspaceMember {
  id: string;
  user_id: string;
  workspace_id: string;
  role: WorkspaceRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  user?: WorkspaceUser;
}

export interface WorkspaceInvitation {
  id: string;
  workspace_id: string;
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

export interface WorkspaceInvitationCreate {
  email: string;
  role: WorkspaceRole;
}

export interface WorkspaceInvitationTokenRequest {
  token: string;
}

export interface WorkspaceInvitationPreview {
  workspace_name: string;
  inviter_email: string;
  invited_email: string;
  role: WorkspaceRole;
  expires_at: string;
}
