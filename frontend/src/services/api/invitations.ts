/**
 * Organization invitation API service for FlowPilot AI.
 *
 * ARCH-04 moved invitation management from the workspace to the organization.
 * Membership is granted at the organization and projected down into
 * workspaces, so the workspace was never the right scope to invite into.
 *
 *   /organizations/{organization_id}/invitations...  management, org-scoped
 *   /invitations/preview, /accept, /reject           token-addressed
 *
 * These functions take an organizationId. They previously took a workspaceId
 * and passed it into an organization-scoped URL builder, which produced a
 * well-formed request against a nonexistent organization and a 404 that read
 * as a permissions failure rather than a wrong-identifier bug.
 */

import apiClient from "@/services/api/client";
import { INVITATION_ENDPOINTS } from "@/services/api/endpoints";

import type {
  InvitationTokenRequest,
  WorkspaceInvitation,
  WorkspaceInvitationAccepted,
  WorkspaceInvitationCreateRequest,
  WorkspaceInvitationPreview,
} from "@/types/tenancy";

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
 * the session identifies the actor. A forwarded link cannot be accepted on
 * someone else's behalf.
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

/** Requires an authenticated session, for the same reason as acceptInvitation. */
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
