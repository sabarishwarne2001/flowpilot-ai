/**
 * ARCH-26 — API client for warehouse destinations, schedules and sync runs.
 *
 * WHY EVERY FUNCTION TAKES `organizationId` EXPLICITLY
 * ====================================================
 *
 * The endpoint builders in endpoints.ts throw on an empty organizationId
 * rather than producing `/organizations//analytics/...`, which the server
 * would answer with a 404 that reads like a missing feature. Passing the id
 * explicitly at every call site keeps that throw close to the mistake.
 *
 * A NOTE ON THE PROBE ENDPOINT
 * ============================
 *
 * `testDestination` resolves on a failed probe. The server returns 200 with
 * `ok: false`, because the probe ran and the answer is the payload — a 5xx
 * there would make the shared Axios error path treat a working feature
 * reporting a bad credential as an outage. Call sites must read `.ok`, not
 * merely `await` without checking.
 */

import apiClient from "@/services/api/client";
import { ANALYTICS_ENDPOINTS } from "@/services/api/endpoints";
import type {
  ConnectionTestResult,
  ConsumptionAnalytics,
  ExportDatasetDescriptor,
  ExportSchedule,
  ExportScheduleCreate,
  ExportScheduleUpdate,
  ExportSyncRun,
  ManualSyncRequest,
  ManualSyncResponse,
  WarehouseDestination,
  WarehouseDestinationCreate,
  WarehouseDestinationUpdate,
} from "@/types/analytics";

const JSON_HEADERS = { Accept: "application/json" } as const;

// ---------------------------------------------------------------------------
// Destinations
// ---------------------------------------------------------------------------

export const listDestinations = async (
  organizationId: string,
): Promise<WarehouseDestination[]> => {
  const response = await apiClient.get<WarehouseDestination[]>(
    ANALYTICS_ENDPOINTS.destinations(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const getDestination = async (
  organizationId: string,
  destinationId: string,
): Promise<WarehouseDestination> => {
  const response = await apiClient.get<WarehouseDestination>(
    ANALYTICS_ENDPOINTS.destination(organizationId, destinationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const createDestination = async (
  organizationId: string,
  payload: WarehouseDestinationCreate,
): Promise<WarehouseDestination> => {
  const response = await apiClient.post<WarehouseDestination>(
    ANALYTICS_ENDPOINTS.destinations(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const updateDestination = async (
  organizationId: string,
  destinationId: string,
  payload: WarehouseDestinationUpdate,
): Promise<WarehouseDestination> => {
  const response = await apiClient.patch<WarehouseDestination>(
    ANALYTICS_ENDPOINTS.destination(organizationId, destinationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const deleteDestination = async (
  organizationId: string,
  destinationId: string,
): Promise<void> => {
  await apiClient.delete(
    ANALYTICS_ENDPOINTS.destination(organizationId, destinationId),
    { headers: JSON_HEADERS },
  );
};

/**
 * Probe a destination.
 *
 * Resolves on failure with `ok: false`. See the module docstring.
 */
export const testDestination = async (
  organizationId: string,
  destinationId: string,
): Promise<ConnectionTestResult> => {
  const response = await apiClient.post<ConnectionTestResult>(
    ANALYTICS_ENDPOINTS.testDestination(organizationId, destinationId),
    undefined,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------

export const listSchedules = async (
  organizationId: string,
): Promise<ExportSchedule[]> => {
  const response = await apiClient.get<ExportSchedule[]>(
    ANALYTICS_ENDPOINTS.schedules(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const createSchedule = async (
  organizationId: string,
  payload: ExportScheduleCreate,
): Promise<ExportSchedule> => {
  const response = await apiClient.post<ExportSchedule>(
    ANALYTICS_ENDPOINTS.schedules(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const updateSchedule = async (
  organizationId: string,
  scheduleId: string,
  payload: ExportScheduleUpdate,
): Promise<ExportSchedule> => {
  const response = await apiClient.patch<ExportSchedule>(
    ANALYTICS_ENDPOINTS.schedule(organizationId, scheduleId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const deleteSchedule = async (
  organizationId: string,
  scheduleId: string,
): Promise<void> => {
  await apiClient.delete(ANALYTICS_ENDPOINTS.schedule(organizationId, scheduleId), {
    headers: JSON_HEADERS,
  });
};

// ---------------------------------------------------------------------------
// Runs and analytics
// ---------------------------------------------------------------------------

export const triggerSync = async (
  organizationId: string,
  payload: ManualSyncRequest,
): Promise<ManualSyncResponse> => {
  const response = await apiClient.post<ManualSyncResponse>(
    ANALYTICS_ENDPOINTS.sync(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const listRuns = async (
  organizationId: string,
  limit = 50,
  destinationId?: string,
): Promise<ExportSyncRun[]> => {
  const response = await apiClient.get<ExportSyncRun[]>(
    ANALYTICS_ENDPOINTS.runs(organizationId),
    {
      headers: JSON_HEADERS,
      params: destinationId
        ? { limit, destination_id: destinationId }
        : { limit },
    },
  );
  return response.data;
};

export const getConsumption = async (
  organizationId: string,
  windowDays = 30,
  granularity: "HOUR" | "DAY" | "MONTH" = "DAY",
): Promise<ConsumptionAnalytics> => {
  const response = await apiClient.get<ConsumptionAnalytics>(
    ANALYTICS_ENDPOINTS.consumption(organizationId),
    {
      headers: JSON_HEADERS,
      params: { window_days: windowDays, granularity },
    },
  );
  return response.data;
};

export const listDatasetDescriptors = async (
  organizationId: string,
): Promise<ExportDatasetDescriptor[]> => {
  const response = await apiClient.get<ExportDatasetDescriptor[]>(
    ANALYTICS_ENDPOINTS.datasets(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};
