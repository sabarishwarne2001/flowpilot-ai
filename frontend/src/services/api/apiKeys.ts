import apiClient from "@/services/api/client";
import { API_KEY_ENDPOINTS } from "@/services/api/endpoints";
import type {
  ApiKeyCreateRequest,
  ApiKeyRead,
  ApiKeyResponse,
} from "@/types/apiKey";

export const listApiKeys = async (
  organizationId: string,
): Promise<ApiKeyRead[]> => {
  const response = await apiClient.get<ApiKeyRead[]>(
    API_KEY_ENDPOINTS.list(organizationId),
  );
  return response.data;
};

export const createApiKey = async (
  organizationId: string,
  data: ApiKeyCreateRequest,
): Promise<ApiKeyResponse> => {
  const response = await apiClient.post<ApiKeyResponse>(
    API_KEY_ENDPOINTS.create(organizationId),
    data,
  );
  return response.data;
};

export const rotateApiKey = async (
  organizationId: string,
  keyId: string,
  force = false,
): Promise<ApiKeyResponse> => {
  const response = await apiClient.post<ApiKeyResponse>(
    API_KEY_ENDPOINTS.rotate(organizationId, keyId),
    { force },
  );
  return response.data;
};

export const revokeApiKey = async (
  organizationId: string,
  keyId: string,
): Promise<ApiKeyRead> => {
  const response = await apiClient.delete<ApiKeyRead>(
    API_KEY_ENDPOINTS.revoke(organizationId, keyId),
  );
  return response.data;
};

export const apiKeysApi = {
  listApiKeys,
  createApiKey,
  rotateApiKey,
  revokeApiKey,
} as const;

export default apiKeysApi;
