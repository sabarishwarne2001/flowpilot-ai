import apiClient from "@/services/api/client";
import { DEVELOPER_ENDPOINTS } from "@/services/api/endpoints";
import type {
  ApiExplorerCatalogue,
  DeveloperKeyCreateRequest,
  DeveloperKeyIssued,
  DeveloperKeyMetrics,
  DeveloperKeySummary,
  DeveloperOverview,
  DeveloperTierUpdateRequest,
  TierCatalogue,
} from "@/types/developer";

/**
 * ARCH-21 — developer platform client.
 *
 * Every call here goes to /organizations/{id}/developer/* and carries the
 * session, because these are console operations performed by a human admin.
 * The backend refuses an API-key principal on all of them: a key that could
 * raise its own tier would be a privilege-escalation primitive, which is why
 * api_keys:write has sat in PERMANENTLY_EXCLUDED_SCOPES since ARCH-08.
 *
 * There is deliberately no client for PUBLIC_API_ENDPOINTS. Those routes
 * authenticate with an API key, not a session, and calling them from the
 * browser would both fail and imply the token belongs in frontend code.
 */

export const getDeveloperOverview = async (
  organizationId: string,
  windowDays = 30,
): Promise<DeveloperOverview> => {
  const response = await apiClient.get<DeveloperOverview>(
    DEVELOPER_ENDPOINTS.overview(organizationId),
    {
      params: { days: windowDays },
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

export const getTierCatalogue = async (
  organizationId: string,
): Promise<TierCatalogue> => {
  const response = await apiClient.get<TierCatalogue>(
    DEVELOPER_ENDPOINTS.tiers(organizationId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const getKeyMetrics = async (
  organizationId: string,
  keyId: string,
  windowDays = 30,
): Promise<DeveloperKeyMetrics> => {
  const response = await apiClient.get<DeveloperKeyMetrics>(
    DEVELOPER_ENDPOINTS.keyMetrics(organizationId, keyId),
    {
      params: { days: windowDays },
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

export const getApiExplorer = async (
  organizationId: string,
  workspaceId?: string,
): Promise<ApiExplorerCatalogue> => {
  const response = await apiClient.get<ApiExplorerCatalogue>(
    DEVELOPER_ENDPOINTS.explorer(organizationId),
    {
      params: workspaceId ? { workspace_id: workspaceId } : undefined,
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

/**
 * Issues a key and returns the token ONCE.
 *
 * The caller must surface `token` immediately and must not persist it. It is
 * not re-fetchable: only an HMAC of the secret is stored, so a lost token is
 * replaced by rotation, never recovered.
 */
export const issueDeveloperKey = async (
  organizationId: string,
  payload: DeveloperKeyCreateRequest,
): Promise<DeveloperKeyIssued> => {
  const response = await apiClient.post<DeveloperKeyIssued>(
    DEVELOPER_ENDPOINTS.keys(organizationId),
    payload,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Reassigns a key's rate tier.
 *
 * A 409 here is the plan ceiling, not a transient failure: the requested tier
 * is above what the organization's quota tier entitles it to. Retrying will
 * produce the same refusal, so the UI surfaces it and stops.
 */
export const updateKeyTier = async (
  organizationId: string,
  keyId: string,
  payload: DeveloperTierUpdateRequest,
): Promise<DeveloperKeySummary> => {
  const response = await apiClient.patch<DeveloperKeySummary>(
    DEVELOPER_ENDPOINTS.keyTier(organizationId, keyId),
    payload,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};
