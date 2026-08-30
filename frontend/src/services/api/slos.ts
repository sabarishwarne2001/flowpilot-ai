import apiClient from "@/services/api/client";
import { SLO_ENDPOINTS } from "@/services/api/endpoints";
import type {
  SLOSummary,
  SLOTarget,
  SLOTargetUpdateRequest,
  SLOWindow,
} from "@/types/slo";

export const getOrganizationSLOs = async (
  organizationId: string,
  period: SLOWindow = "DAY",
): Promise<SLOSummary> => {
  const response = await apiClient.get<SLOSummary>(
    SLO_ENDPOINTS.list(organizationId),
    {
      params: { period },
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

export const setOrganizationSLO = async (
  organizationId: string,
  sloKey: string,
  payload: SLOTargetUpdateRequest,
): Promise<SLOTarget> => {
  const response = await apiClient.put<SLOTarget>(
    SLO_ENDPOINTS.detail(organizationId, sloKey),
    payload,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const clearOrganizationSLO = async (
  organizationId: string,
  sloKey: string,
): Promise<void> => {
  await apiClient.delete(SLO_ENDPOINTS.detail(organizationId, sloKey));
};
