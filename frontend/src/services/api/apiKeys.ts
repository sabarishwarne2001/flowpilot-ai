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

/*
 * The apiKeysApi namespace wrapper was removed here, with its default export.
 *
 * It was an object literal re-exporting the named functions above, and it had
 * exactly two references in the whole repository: its own declaration and its
 * own `export default`. Every consumer imports the standalone functions
 * directly, which is why it could sit here for months looking like API
 * surface while being reachable from nothing.
 *
 * Safe to delete precisely because of that count -- anything importing it
 * would have appeared in the same grep that found it dead.
 */
