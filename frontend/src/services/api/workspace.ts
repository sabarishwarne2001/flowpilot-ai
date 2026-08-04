import apiClient from "./client";

import type {
  Workspace,
  WorkspaceCreate,
  WorkspaceMember,
  WorkspaceInvitation,
  WorkspaceInvitationCreate,
  WorkspaceInvitationList,
  WorkspaceInvitationPreview,
} from "@/types/workspace";

export const getWorkspace = async (): Promise<Workspace | null> => {
  const response = await apiClient.get("/workspace");
  if (response.status === 204) return null;
  return response.data;
};

export const getPublicWorkspace = async (): Promise<Workspace> => {
  const response = await apiClient.get("/workspace/public");
  return response.data;
};

export const saveWorkspace = async (
  payload: WorkspaceCreate,
): Promise<Workspace> => {
  const response = await apiClient.put(
    "/workspace",
    payload,
  );

  return response.data;
};

// ============================================================================
// Memberships (Sprint 2 Extensions)
// ============================================================================

export const listWorkspaceMembers = async (): Promise<WorkspaceMember[]> => {
  const response = await apiClient.get<WorkspaceMember[]>("/workspace/members");
  return response.data;
};

export const getMyMembership = async (): Promise<WorkspaceMember> => {
  const response = await apiClient.get<WorkspaceMember>("/workspace/members/me");
  return response.data;
};

export const removeWorkspaceMember = async (memberUserId: string): Promise<void> => {
  await apiClient.delete(`/workspace/members/${memberUserId}`);
};

// ============================================================================
// Invitations (Sprint 2 Extensions)
// ============================================================================

export const previewInvitation = async (token: string): Promise<WorkspaceInvitationPreview> => {
  const response = await apiClient.get<WorkspaceInvitationPreview>("/workspace/invitations/preview", {
    params: { token },
  });
  return response.data;
};

export const listMyInvitations = async (): Promise<WorkspaceInvitation[]> => {
  const response = await apiClient.get<WorkspaceInvitation[]>("/workspace/invitations/me");
  return response.data;
};

export const inviteUser = async (
  invitation: WorkspaceInvitationCreate
): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>("/workspace/invite", invitation);
  return response.data;
};

export const listPendingInvitations = async (): Promise<WorkspaceInvitationList> => {
  const response = await apiClient.get<WorkspaceInvitationList>("/workspace/invitations/pending");
  return response.data;
};

export const revokeInvitation = async (invitationId: string): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>(
    `/workspace/invitations/${invitationId}/revoke`
  );
  return response.data;
};

export const resendInvitation = async (invitationId: string): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>(
    `/workspace/invitations/${invitationId}/resend`
  );
  return response.data;
};

export const acceptInvitation = async (token: string): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>("/workspace/invitations/accept", {
    token,
  });
  return response.data;
};

export const rejectInvitation = async (token: string): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>("/workspace/invitations/reject", {
    token,
  });
  return response.data;
};
