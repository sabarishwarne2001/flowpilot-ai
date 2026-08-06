/**
 * Workspace API service for FlowPilot AI.
 *
 * Replaces the tenant portion of the legacy services/api/workspace.ts, which
 * called GET /workspace and PUT /workspace — endpoints deleted in ARCH-01
 * because they resolved "the user's workspace" from a single active
 * membership, an assumption that returned HTTP 500 for any account holding
 * two.
 *
 * Every call names its workspace explicitly. That is the ARCH-01 contract: the
 * tenant identifier is untrusted input the server validates against the
 * actor's membership, not something hidden from the client. Withholding it
 * only removed the server's ability to know which tenant the caller meant.
 *
 * Creating a workspace lives in organization.ts, because creation is governed
 * by organization role rather than workspace role.
 */

import apiClient from "@/services/api/client";
import { WORKSPACE_ENDPOINTS } from "@/services/api/endpoints";

import type {
  SlugAvailability,
  Workspace,
  WorkspaceMember,
  WorkspaceMemberGrantRequest,
  WorkspaceMemberList,
  WorkspaceMemberRoleUpdateRequest,
  WorkspaceUpdateRequest,
} from "@/types/tenancy";

/* ==========================================================================
 * Workspace
 * ========================================================================== */

/**
 * Returns the addressed workspace.
 *
 * Any effective role may read it. A caller with no access receives 404
 * (RESOURCE_NOT_FOUND), not 403.
 */
export const getWorkspaceById = async (
  workspaceId: string,
): Promise<Workspace> => {
  const response = await apiClient.get<Workspace>(
    WORKSPACE_ENDPOINTS.detail(workspaceId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Updates workspace name, locale, and branding.
 *
 * Omitted fields are left unchanged. Clearing the logo uses
 * removeWorkspaceLogo — passing null here means "unchanged", not "remove".
 */
export const updateWorkspaceById = async (
  workspaceId: string,
  data: WorkspaceUpdateRequest,
): Promise<Workspace> => {
  const response = await apiClient.patch<Workspace>(
    WORKSPACE_ENDPOINTS.detail(workspaceId),
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/** Clears the workspace logo reference. */
export const removeWorkspaceLogo = async (
  workspaceId: string,
): Promise<Workspace> => {
  const response = await apiClient.delete<Workspace>(
    WORKSPACE_ENDPOINTS.logo(workspaceId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Advisory slug availability check, scoped to the parent organization.
 *
 * Two tenants may both have a workspace called "engineering": uniqueness is
 * per organization, not global.
 */
export const checkWorkspaceSlug = async (
  workspaceId: string,
  slug: string,
): Promise<SlugAvailability> => {
  const response = await apiClient.get<SlugAvailability>(
    WORKSPACE_ENDPOINTS.slugAvailable(workspaceId),
    {
      params: { slug },
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

/**
 * Soft-deletes the workspace.
 *
 * Authorized at organization level: a workspace does not own itself, so its
 * destruction is a decision for the tenant that does. Rejects if this is the
 * organization's last active workspace.
 */
export const archiveWorkspace = async (
  workspaceId: string,
): Promise<Workspace> => {
  const response = await apiClient.post<Workspace>(
    WORKSPACE_ENDPOINTS.archive(workspaceId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/** Restores an archived workspace. */
export const restoreWorkspace = async (
  workspaceId: string,
): Promise<Workspace> => {
  const response = await apiClient.post<Workspace>(
    WORKSPACE_ENDPOINTS.restore(workspaceId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/* ==========================================================================
 * Members
 * ========================================================================== */

/**
 * Returns everyone with access to this workspace.
 *
 * The response merges explicit grants with organization OWNER and ADMIN
 * members holding DERIVED admin. Derived entries carry is_derived === true and
 * id === null: there is no membership row to reference, and revocation
 * controls must be disabled for them because the access follows from the
 * organization role and is changed there.
 */
export const listWorkspaceMembers = async (
  workspaceId: string,
): Promise<WorkspaceMemberList> => {
  const response = await apiClient.get<WorkspaceMemberList>(
    WORKSPACE_ENDPOINTS.members(workspaceId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Grants an existing organization member access to this workspace.
 *
 * The target must already hold an ACTIVE organization membership — the seat is
 * what authorizes their presence in the tenant. Otherwise rejects with
 * WORKSPACE_MEMBER_ERROR.
 *
 * Granting ADMIN additionally requires organization-level standing.
 */
export const grantWorkspaceAccess = async (
  workspaceId: string,
  data: WorkspaceMemberGrantRequest,
): Promise<WorkspaceMember> => {
  const response = await apiClient.post<WorkspaceMember>(
    WORKSPACE_ENDPOINTS.members(workspaceId),
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Changes an existing workspace grant.
 *
 * @param membershipId - A real membership identifier. Derived entries have
 *   id === null and cannot be modified here; change the organization role
 *   instead.
 */
export const changeWorkspaceMemberRole = async (
  workspaceId: string,
  membershipId: string,
  data: WorkspaceMemberRoleUpdateRequest,
): Promise<WorkspaceMember> => {
  const response = await apiClient.patch<WorkspaceMember>(
    WORKSPACE_ENDPOINTS.member(workspaceId, membershipId),
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Revokes a workspace grant, retaining the row.
 *
 * The organization seat is untouched: losing access to one workspace is not
 * the same as leaving the company.
 */
export const revokeWorkspaceAccess = async (
  workspaceId: string,
  membershipId: string,
): Promise<WorkspaceMember> => {
  const response = await apiClient.post<WorkspaceMember>(
    WORKSPACE_ENDPOINTS.revokeMember(workspaceId, membershipId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Removes the acting user's own workspace grant.
 *
 * An organization administrator retains derived access afterward, so this
 * removes them from the member list without cutting them off — matching
 * GitHub, where an organization owner cannot lock themselves out of a
 * repository they administer.
 */
export const leaveWorkspace = async (
  workspaceId: string,
): Promise<{ message: string }> => {
  const response = await apiClient.post<{ message: string }>(
    WORKSPACE_ENDPOINTS.leave(workspaceId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const workspaceApi = {
  getWorkspaceById,
  updateWorkspaceById,
  removeWorkspaceLogo,
  checkWorkspaceSlug,
  archiveWorkspace,
  restoreWorkspace,
  listWorkspaceMembers,
  grantWorkspaceAccess,
  changeWorkspaceMemberRole,
  revokeWorkspaceAccess,
  leaveWorkspace,
};

export default workspaceApi;
