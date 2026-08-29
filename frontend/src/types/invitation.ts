export interface WorkspacePreviewEntry {
  readonly name: string;
  readonly role: string;
}

export interface MyPendingInvitation {
  readonly organization_name: string;
  readonly organization_role: string;
  readonly inviter_email: string;
  readonly workspaces: readonly WorkspacePreviewEntry[];
  readonly expires_at: string;
}

export interface MyPendingInvitationsResponse {
  readonly items: readonly MyPendingInvitation[];
}
