import apiClient from "@/services/api/client";
import type {
  VerificationDetailResponse,
  VerificationListParams,
  VerificationResolveRequest,
  VerificationSummaryResponse,
} from "@/types/verification";

const seg = (value: string): string => encodeURIComponent(value);

const ws = (workspaceId: string): string => {
  if (!workspaceId) {
    throw new Error(
      "A workspaceId is required to build this URL. Gate the query with `enabled: Boolean(workspaceId)`.",
    );
  }
  return seg(workspaceId);
};

export const VERIFICATION_ENDPOINTS = {
  list: (workspaceId: string) => `/workspaces/${ws(workspaceId)}/verifications`,
  detail: (workspaceId: string, verificationId: string) =>
    `/workspaces/${ws(workspaceId)}/verifications/${seg(verificationId)}`,
  resolve: (workspaceId: string, verificationId: string) =>
    `/workspaces/${ws(workspaceId)}/verifications/${seg(verificationId)}/resolve`,
} as const;

export const listVerifications = async (
  workspaceId: string,
  params: VerificationListParams = {},
): Promise<VerificationSummaryResponse[]> => {
  const response = await apiClient.get<VerificationSummaryResponse[]>(
    VERIFICATION_ENDPOINTS.list(workspaceId),
    {
      params: {
        ...(params.status ? { status: params.status } : {}),
        ...(params.skip !== undefined ? { skip: params.skip } : {}),
        ...(params.limit !== undefined ? { limit: params.limit } : {}),
      },
    },
  );
  return response.data;
};

export const getVerification = async (
  workspaceId: string,
  verificationId: string,
): Promise<VerificationDetailResponse> => {
  const response = await apiClient.get<VerificationDetailResponse>(
    VERIFICATION_ENDPOINTS.detail(workspaceId, verificationId),
  );
  return response.data;
};

export const resolveVerification = async (
  workspaceId: string,
  verificationId: string,
  payload: VerificationResolveRequest,
): Promise<VerificationDetailResponse> => {
  const response = await apiClient.post<VerificationDetailResponse>(
    VERIFICATION_ENDPOINTS.resolve(workspaceId, verificationId),
    payload,
  );
  return response.data;
};

/*
 * The verificationApi namespace wrapper was removed here, with its default export.
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
