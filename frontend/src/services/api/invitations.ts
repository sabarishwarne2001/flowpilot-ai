/**
 * Workspace invitation API service for FlowPilot AI.
 *
 * Route shapes reflect what establishes context:
 *
 *   /workspaces/{workspace_id}/invitations...  management, tenant-resolved
 *   /invitations/preview, /accept, /reject     token-addressed
 *
 * previewInvitation is PUBLIC — a recipient must see who invited them and to
 * what before creating an account.
 *
 * acceptInvitation and rejectInvitation are NOT. Before ARCH-01 both took only
 * a token and the server resolved the user by the invited email address, so
 * any holder of a forwarded link could accept on the invitee's behalf, and
 * reject in particular gave a token holder a denial of service on the
 * invitation. The token identifies the invitation; the session identifies the
 * actor. Calling either without an authenticated session now rejects with
 * ApiError(401).
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
 * Workspace-scoped management
 * ========================================================================== */

/**
 * Invites a user to this workspace.
 *
 * The server enforces that the invited role sits at or below the inviter's own
 * authority, and that inviting at workspace ADMIN requires organization-level
 * standing. Use permissions.canAssignWorkspaceRole to decide which options to
 * offer, so the form never presents a role the request will reject.
 *
 * Rejects with INVITATION_ALREADY_MEMBER if the recipient already has access.
 */
export const createInvitation = async (
  workspaceId: string,
  data: WorkspaceInvitationCreateRequest,
): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>(
    INVITATION_ENDPOINTS.create(workspaceId),
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Returns pending invitations for this workspace.
 *
 * Expiry is evaluated lazily on the server, so an entry may already be past
 * expires_at while still listed as pending. Filter on expires_at when
 * rendering rather than trusting the status alone.
 */
export const listPendingInvitations = async (
  workspaceId: string,
): Promise<WorkspaceInvitation[]> => {
  const response = await apiClient.get<WorkspaceInvitation[]>(
    INVITATION_ENDPOINTS.list(workspaceId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/** Revokes a pending invitation. */
export const revokeInvitation = async (
  workspaceId: string,
  invitationId: string,
): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>(
    INVITATION_ENDPOINTS.revoke(workspaceId, invitationId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Reissues an invitation with a fresh token and expiry.
 *
 * Every authorization check is re-run, so a resend cannot preserve a role the
 * actor is no longer permitted to grant. The returned invitation has a new
 * identifier: the original is revoked.
 */
export const resendInvitation = async (
  workspaceId: string,
  invitationId: string,
): Promise<WorkspaceInvitation> => {
  const response = await apiClient.post<WorkspaceInvitation>(
    INVITATION_ENDPOINTS.resend(workspaceId, invitationId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/* ==========================================================================
 * Token-addressed recipient actions
 * ========================================================================== */

/**
 * Resolves an invitation for public display.
 *
 * Public by design and safe to call before authentication. Returns no database
 * identifiers.
 *
 * Rejects with INVALID_INVITATION_TOKEN, INVITATION_EXPIRED, or
 * INVITATION_ALREADY_PROCESSED — three distinct codes so the page can explain
 * precisely what happened rather than showing one generic dead end.
 */
export const previewInvitation = async (
  token: string,
): Promise<WorkspaceInvitationPreview> => {
  const response = await apiClient.get<WorkspaceInvitationPreview>(
    INVITATION_ENDPOINTS.preview,
    {
      params: { token },
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

/**
 * Accepts an invitation on behalf of the authenticated actor.
 *
 * Requires a session. Rejects with:
 *   UNAUTHORIZED               not signed in
 *   INVITATION_EMAIL_MISMATCH  signed in as someone other than the invitee —
 *                              offer to sign out and switch accounts
 *   INVITATION_ALREADY_MEMBER  already has access
 *
 * On success the server has provisioned an organization seat AND a workspace
 * grant in one transaction, and the response carries the destination so the
 * caller can navigate straight in without a follow-up bootstrap call.
 */
export const acceptInvitation = async (
  token: string,
): Promise<WorkspaceInvitationAccepted> => {
  const payload: InvitationTokenRequest = { token };

  const response = await apiClient.post<WorkspaceInvitationAccepted>(
    INVITATION_ENDPOINTS.accept,
    payload,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Declines an invitation on behalf of the authenticated actor.
 *
 * Authenticated for the same reason as acceptance: unauthenticated rejection
 * let any token holder burn an invitation the recipient had not yet seen.
 */
export const rejectInvitation = async (
  token: string,
): Promise<WorkspaceInvitation> => {
  const payload: InvitationTokenRequest = { token };

  const response = await apiClient.post<WorkspaceInvitation>(
    INVITATION_ENDPOINTS.reject,
    payload,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const invitationApi = {
  createInvitation,
  listPendingInvitations,
  revokeInvitation,
  resendInvitation,
  previewInvitation,
  acceptInvitation,
  rejectInvitation,
};

export default invitationApi;
