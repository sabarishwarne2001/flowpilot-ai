import apiClient from "@/services/api/client";
import { BYOK_ENDPOINTS } from "@/services/api/endpoints";
import type {
  BYOKOverviewResponse,
  BYOKProvider,
  BYOKSavingsResponse,
  BYOKTaskType,
  CredentialValidationResponse,
  FallbackPolicyUpdate,
  ModelRouteResponse,
  ModelRouteUpsert,
  ProviderCatalogEntry,
  ProviderCredentialResponse,
  ProviderCredentialUpsert,
} from "@/types/byok";

const JSON_HEADERS = { Accept: "application/json" } as const;

export const getBYOKOverview = async (
  organizationId: string,
  windowDays: number,
): Promise<BYOKOverviewResponse> => {
  const response = await apiClient.get<BYOKOverviewResponse>(
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
): Promise<ProviderCredentialResponse[]> => {
  const response = await apiClient.get<ProviderCredentialResponse[]>(
    BYOK_ENDPOINTS.credentials(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const upsertCredential = async (
  organizationId: string,
  payload: ProviderCredentialUpsert,
): Promise<ProviderCredentialResponse> => {
  const response = await apiClient.put<ProviderCredentialResponse>(
    BYOK_ENDPOINTS.credentials(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const deleteCredential = async (
  organizationId: string,
  provider: BYOKProvider,
): Promise<ProviderCredentialResponse> => {
  const response = await apiClient.delete<ProviderCredentialResponse>(
    BYOK_ENDPOINTS.credential(organizationId, provider),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const validateCredential = async (
  organizationId: string,
  provider: BYOKProvider,
): Promise<CredentialValidationResponse> => {
  const response = await apiClient.post<CredentialValidationResponse>(
    BYOK_ENDPOINTS.validate(organizationId, provider),
    undefined,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const updateFallbackPolicy = async (
  organizationId: string,
  provider: BYOKProvider,
  payload: FallbackPolicyUpdate,
): Promise<ProviderCredentialResponse> => {
  const response = await apiClient.put<ProviderCredentialResponse>(
    BYOK_ENDPOINTS.fallback(organizationId, provider),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const listModelRoutes = async (
  organizationId: string,
): Promise<ModelRouteResponse[]> => {
  const response = await apiClient.get<ModelRouteResponse[]>(
    BYOK_ENDPOINTS.routes(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const upsertModelRoute = async (
  organizationId: string,
  payload: ModelRouteUpsert,
): Promise<ModelRouteResponse> => {
  const response = await apiClient.put<ModelRouteResponse>(
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
): Promise<BYOKSavingsResponse> => {
  const response = await apiClient.get<BYOKSavingsResponse>(
    BYOK_ENDPOINTS.savings(organizationId),
    { headers: JSON_HEADERS, params: { window_days: windowDays } },
  );
  return response.data;
};
