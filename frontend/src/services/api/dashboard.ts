import apiClient from "@/services/api/client";
import { DASHBOARD_ENDPOINTS } from "@/services/api/endpoints";
import type { DashboardMetricsResponse } from "@/types/dashboard";

const JSON_HEADERS = { Accept: "application/json" } as const;

export const getDashboardOverview = async (
  workspaceId: string,
): Promise<DashboardMetricsResponse> => {
  const response = await apiClient.get<DashboardMetricsResponse>(
    DASHBOARD_ENDPOINTS.overview(workspaceId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const getDashboardHealth = async (): Promise<void> => Promise.resolve();

export const dashboardApi = { getDashboardOverview, getDashboardHealth };
export default dashboardApi;
