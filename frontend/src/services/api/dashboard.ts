import apiClient from "@/services/api/client";
import type { DashboardMetricsResponse } from "@/types/dashboard";

/* --------------------------------------------------------------------------
 * API Endpoints
 * -------------------------------------------------------------------------- */

export const DASHBOARD_ENDPOINTS = {
  overview: "/dashboard/overview",
  health: "/dashboard/health",
} as const;

/**
 * Returns dashboard KPI metrics and recent activity.
 *
 * Cache management, polling, and revalidation are intentionally handled
 * by TanStack Query rather than this API service.
 */
export const getDashboardOverview =
  async (): Promise<DashboardMetricsResponse> => {
    const response =
      await apiClient.get<DashboardMetricsResponse>(
        DASHBOARD_ENDPOINTS.overview,
        {
          headers: {
            Accept: "application/json",
          },
        },
      );

    return response.data;
  };

/**
 * Placeholder for future dashboard/system health endpoint.
 */
export const getDashboardHealth =
  async (): Promise<void> => {
    return Promise.resolve();
  };

/**
 * Unified Dashboard API namespace.
 */
export const dashboardApi = {
  getDashboardOverview,
  getDashboardHealth,
};

export default dashboardApi;
