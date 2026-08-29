/**
 * Organization invitation API service for FlowPilot AI.
 */

import apiClient from "@/services/api/client";
import {
  INVITATION_ENDPOINTS,
  ME_INVITATION_ENDPOINTS,
} from "@/services/api/endpoints";

import type {
  InvitationTokenRequest,
  WorkspaceInvitation,
  WorkspaceInvitationAccepted,
  WorkspaceInvitationCreateRequest,
  WorkspaceInvitationPreview,
} from "@/types/tenancy";
import type { MyPendingInvitationsResponse } from "@/types/invitation";

/* ==========================================================================
 * Organization-scoped management
 * ========================================================================== */

export const createInvitation = async (
  organizationId: string,
  data: WorkspaceInvitationCreateRequest,
): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>(
    INVITATION_ENDPOINTS.create(organizationId),
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const listPendingInvitations = async (
  organizationId: string,
): Promise<WorkspaceInvitation[]> => {
  const response = await apiClient.get<WorkspaceInvitation[]>(
    INVITATION_ENDPOINTS.list(organizationId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const revokeInvitation = async (
  organizationId: string,
  invitationId: string,
): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>(
    INVITATION_ENDPOINTS.revoke(organizationId, invitationId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const resendInvitation = async (
  organizationId: string,
  invitationId: string,
): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>(
    INVITATION_ENDPOINTS.resend(organizationId, invitationId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/* ==========================================================================
 * Token-addressed recipient actions (public / session-authenticated)
 * ========================================================================== */

/** Public. A recipient must see who invited them before creating an account. */
export const previewInvitation = async (
  token: string,
): Promise<WorkspaceInvitationPreview> => {
  const response = await apiClient.post<WorkspaceInvitationPreview>(
    INVITATION_ENDPOINTS.preview,
    { token } satisfies InvitationTokenRequest,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Requires an authenticated session. The token identifies the invitation;
 * the session identifies the actor.
 */
export const acceptInvitation = async (
  token: string,
): Promise<WorkspaceInvitationAccepted> => {
  const response = await apiClient.post<WorkspaceInvitationAccepted>(
    INVITATION_ENDPOINTS.accept,
    { token } satisfies InvitationTokenRequest,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const rejectInvitation = async (
  token: string,
): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>(
    INVITATION_ENDPOINTS.reject,
    { token } satisfies InvitationTokenRequest,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/* ==========================================================================
 * User-scoped pending invitations list
 * ========================================================================== */

export const listMyInvitations = async (): Promise<MyPendingInvitationsResponse> => {
  const response = await apiClient.get<MyPendingInvitationsResponse>(
    ME_INVITATION_ENDPOINTS.mine,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const invitationsApi = {
  createInvitation,
  listPendingInvitations,
  revokeInvitation,
  resendInvitation,
  previewInvitation,
  acceptInvitation,
  rejectInvitation,
  listMyInvitations,
} as const;

export default invitationsApi;
