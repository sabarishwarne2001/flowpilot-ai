/**
 * Organization API service for FlowPilot AI.
 *
 * The commercial tenant surface: provisioning, settings, the member directory,
 * role management, and ownership transfer.
 *
 * createOrganization replaces onboarding. The pre-ARCH-01 flow used a single
 * PUT endpoint that both created and updated workspaces, which is why an
 * existing owner revisiting the onboarding screen silently overwrote their
 * live settings. Creation and update are now distinct calls with distinct
 * authorization.
 *
 * Every function rejects with ApiError carrying a stable code from
 * @/constants/errorCodes. Branch on the code; display the message.
 */

import apiClient from "@/services/api/client";
import { ORGANIZATION_ENDPOINTS } from "@/services/api/endpoints";

import type {
  Organization,
  OrganizationCreateRequest,
  OrganizationMember,
  OrganizationMemberList,
  OrganizationMemberRoleUpdateRequest,
  OrganizationUpdateRequest,
  OwnershipTransferRequest,
  SlugAvailability,
  Workspace,
  WorkspaceCreateRequest,
} from "@/types/tenancy";

/* ==========================================================================
 * Provisioning and discovery
 * ========================================================================== */

/**
 * Provisions a tenant: an organization, its first workspace, and the founder's
 * memberships in both. Atomic on the server.
 *
 * Requires only an authenticated account. Founding an organization is an
 * account-level capability, not a role permission — which is why no permission
 * helper guards this call.
 *
 * Rejects with ORGANIZATION_ALREADY_EXISTS if the slug was claimed
 * concurrently, or INVALID_SLUG / SLUG_RESERVED if an explicit slug was
 * supplied and is unusable.
 */
export const createOrganization = async (
  data: OrganizationCreateRequest,
): Promise<Organization> => {
  const response = await apiClient.post<Organization>(
    ORGANIZATION_ENDPOINTS.create,
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Advisory slug availability check for the creation form.
 *
 * Advisory only: two concurrent requests can both observe a free slug, so
 * createOrganization may still reject with ORGANIZATION_ALREADY_EXISTS. Treat
 * this as inline form feedback, never as a guarantee.
 */
export const checkOrganizationSlug = async (
  slug: string,
): Promise<SlugAvailability> => {
  const response = await apiClient.get<SlugAvailability>(
    ORGANIZATION_ENDPOINTS.slugAvailable,
    {
      params: { slug },
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

/**
 * Returns the addressed organization.
 *
 * A non-member receives 404 (RESOURCE_NOT_FOUND), not 403 — the server will
 * not confirm that a tenant exists to someone who cannot reach it.
 */
export const getOrganization = async (
  organizationId: string,
): Promise<Organization> => {
  const response = await apiClient.get<Organization>(
    ORGANIZATION_ENDPOINTS.detail(organizationId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Updates organization identity and branding.
 *
 * Omitted fields are left unchanged. Changing the slug changes the tenant's
 * public URL and the previous address stops resolving immediately.
 */
export const updateOrganization = async (
  organizationId: string,
  data: OrganizationUpdateRequest,
): Promise<Organization> => {
  const response = await apiClient.patch<Organization>(
    ORGANIZATION_ENDPOINTS.detail(organizationId),
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Soft-deletes the organization.
 *
 * Not a DELETE: this is a reversible status transition, and data is retained
 * for the contractual retention window.
 */
export const archiveOrganization = async (
  organizationId: string,
): Promise<Organization> => {
  const response = await apiClient.post<Organization>(
    ORGANIZATION_ENDPOINTS.archive(organizationId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/* ==========================================================================
 * Workspaces within an organization
 * ========================================================================== */

/**
 * Returns the workspaces the actor may enter within this organization.
 *
 * Organization OWNER and ADMIN receive every workspace through their derived
 * grant; everyone else receives only those they hold an explicit grant on. The
 * server applies that rule — do not filter the result client-side.
 */
export const listOrganizationWorkspaces = async (
  organizationId: string,
): Promise<Workspace[]> => {
  const response = await apiClient.get<Workspace[]>(
    ORGANIZATION_ENDPOINTS.workspaces(organizationId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Creates an additional workspace inside this organization.
 *
 * Governed by organization role, not workspace role: a workspace does not
 * create itself, and its parent tenant decides what exists inside it.
 */
export const createWorkspace = async (
  organizationId: string,
  data: WorkspaceCreateRequest,
): Promise<Workspace> => {
  const response = await apiClient.post<Workspace>(
    ORGANIZATION_ENDPOINTS.workspaces(organizationId),
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/* ==========================================================================
 * Members
 * ========================================================================== */

/**
 * Returns the member directory with the current seat count.
 *
 * seats_consumed includes pending invitations, which reserve a seat so a
 * tenant cannot over-invite past its plan limit.
 *
 * @param includeInactive - Include deactivated members. Administrators only;
 *   a non-administrator passing true receives 403.
 */
export const listOrganizationMembers = async (
  organizationId: string,
  includeInactive = false,
): Promise<OrganizationMemberList> => {
  const response = await apiClient.get<OrganizationMemberList>(
    ORGANIZATION_ENDPOINTS.members(organizationId),
    {
      params: { include_inactive: includeInactive },
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

/**
 * Changes a member's organization role.
 *
 * The server enforces both halves: the actor must outrank the member as they
 * stand, and must be permitted to assign the new role. Demoting the last owner
 * rejects with LAST_OWNER.
 */
export const changeOrganizationMemberRole = async (
  organizationId: string,
  membershipId: string,
  data: OrganizationMemberRoleUpdateRequest,
): Promise<OrganizationMember> => {
  const response = await apiClient.patch<OrganizationMember>(
    ORGANIZATION_ENDPOINTS.member(organizationId, membershipId),
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Removes a member from the organization, retaining the record.
 *
 * Every workspace grant they held in this organization is revoked in the same
 * transaction, so no orphaned access survives.
 */
export const deactivateOrganizationMember = async (
  organizationId: string,
  membershipId: string,
): Promise<OrganizationMember> => {
  const response = await apiClient.post<OrganizationMember>(
    ORGANIZATION_ENDPOINTS.deactivateMember(organizationId, membershipId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Removes the acting user from the organization.
 *
 * A sole owner rejects with LAST_OWNER. Unlike the pre-ARCH-01 message that
 * named a nonexistent feature, transferOrganizationOwnership below is a real
 * path out.
 */
export const leaveOrganization = async (
  organizationId: string,
): Promise<{ message: string }> => {
  const response = await apiClient.post<{ message: string }>(
    ORGANIZATION_ENDPOINTS.leave(organizationId),
    null,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Transfers ownership to another active member.
 *
 * Promotes the target to OWNER and demotes the caller to ADMIN, in one
 * transaction. The caller is not removed: losing the organization and losing
 * ownership of it are different intentions.
 *
 * @returns The newly promoted owner's membership.
 */
export const transferOrganizationOwnership = async (
  organizationId: string,
  data: OwnershipTransferRequest,
): Promise<OrganizationMember> => {
  const response = await apiClient.post<OrganizationMember>(
    ORGANIZATION_ENDPOINTS.transferOwnership(organizationId),
    data,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const organizationApi = {
  createOrganization,
  checkOrganizationSlug,
  getOrganization,
  updateOrganization,
  archiveOrganization,
  listOrganizationWorkspaces,
  createWorkspace,
  listOrganizationMembers,
  changeOrganizationMemberRole,
  deactivateOrganizationMember,
  leaveOrganization,
  transferOrganizationOwnership,
};

export default organizationApi;
