import apiClient from "@/services/api/client";
import { BYOK_ENDPOINTS } from "@/services/api/endpoints";
import type {
  BYOKOverview,
  BYOKProvider,
  BYOKSavings,
  BYOKTaskType,
  CredentialUpsertRequest,
  CredentialValidation,
  FallbackPolicyRequest,
  ModelRoute,
  ModelRouteUpsertRequest,
  ProviderCatalogEntry,
  ProviderCredential,
} from "@/types/byok";

const JSON_HEADERS = { Accept: "application/json" } as const;

export const getBYOKOverview = async (
  organizationId: string,
  windowDays: number,
): Promise<BYOKOverview> => {
  const response = await apiClient.get<BYOKOverview>(
    BYOK_ENDPOINTS.overview(organizationId),
    { headers: JSON_HEADERS, params: { window_days: windowDays } },
  );
  return response.data;
};

export const listBYOKProviders = async (
  organizationId: string,
): Promise<ProviderCatalogEntry[]> => {
  const response = await apiClient.get<ProviderCatalogEntry[]>(
    BYOK_ENDPOINTS.providers(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const listCredentials = async (
  organizationId: string,
): Promise<ProviderCredential[]> => {
  const response = await apiClient.get<ProviderCredential[]>(
    BYOK_ENDPOINTS.credentials(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

/**
 * Store or rotate a key.
 *
 * The payload is built here and passed straight to axios; it is never held in
 * component state beyond the controlled input, and the caller is expected to
 * clear that input on success. The response contains no key material, so
 * nothing sensitive enters the TanStack Query cache.
 */
export const upsertCredential = async (
  organizationId: string,
  payload: CredentialUpsertRequest,
): Promise<ProviderCredential> => {
  const response = await apiClient.put<ProviderCredential>(
    BYOK_ENDPOINTS.credentials(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const deleteCredential = async (
  organizationId: string,
  provider: BYOKProvider,
): Promise<ProviderCredential> => {
  const response = await apiClient.delete<ProviderCredential>(
    BYOK_ENDPOINTS.credential(organizationId, provider),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const validateCredential = async (
  organizationId: string,
  provider: BYOKProvider,
): Promise<CredentialValidation> => {
  const response = await apiClient.post<CredentialValidation>(
    BYOK_ENDPOINTS.validate(organizationId, provider),
    undefined,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const updateFallbackPolicy = async (
  organizationId: string,
  provider: BYOKProvider,
  payload: FallbackPolicyRequest,
): Promise<ProviderCredential> => {
  const response = await apiClient.put<ProviderCredential>(
    BYOK_ENDPOINTS.fallback(organizationId, provider),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const listModelRoutes = async (
  organizationId: string,
): Promise<ModelRoute[]> => {
  const response = await apiClient.get<ModelRoute[]>(
    BYOK_ENDPOINTS.routes(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const upsertModelRoute = async (
  organizationId: string,
  payload: ModelRouteUpsertRequest,
): Promise<ModelRoute> => {
  const response = await apiClient.put<ModelRoute>(
    BYOK_ENDPOINTS.routes(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const deleteModelRoute = async (
  organizationId: string,
  taskType: BYOKTaskType,
): Promise<void> => {
  await apiClient.delete(BYOK_ENDPOINTS.route(organizationId, taskType), {
    headers: JSON_HEADERS,
  });
};

export const getBYOKSavings = async (
  organizationId: string,
  windowDays: number,
): Promise<BYOKSavings> => {
  const response = await apiClient.get<BYOKSavings>(
    BYOK_ENDPOINTS.savings(organizationId),
    { headers: JSON_HEADERS, params: { window_days: windowDays } },
  );
  return response.data;
};
