/** ARCH-34 — Forensic Audit Radar API client. */

import { apiClient } from "@/services/api/client";
import type {
  AnomalyFeed,
  AnomalyFilters,
  AnomalyFindingDetail,
  AnomalySuppression,
  PriceSeries,
} from "@/types/radar";

const base = (workspaceId: string): string =>
  `/workspaces/${encodeURIComponent(workspaceId)}/anomalies`;

/**
 * The one place a serialised Decimal becomes a number for display.
 *
 * Done here rather than in each component so there is a single answer to
 * "what does 0.88281 look like on screen", and so a component can never
 * accidentally do arithmetic on the string.
 */
export const percent = (score: string | null | undefined): number =>
  score === null || score === undefined ? 0 : Math.round(Number(score) * 100);

export const microsToUnits = (micros: number): number => micros / 1_000_000;

export const listAnomalies = async (
  workspaceId: string,
  filters: AnomalyFilters = {},
): Promise<AnomalyFeed> => {
  const params: Record<string, string> = {};
  if (filters.kind !== undefined) {params["kind"] = filters.kind;}
  if (filters.severity !== undefined) {params["severity"] = filters.severity;}
  if (filters.status !== undefined) {params["status"] = filters.status;}

  const { data } = await apiClient.get<AnomalyFeed>(base(workspaceId), { params });
  return data;
};

export const getAnomaly = async (
  workspaceId: string,
  findingId: string,
): Promise<AnomalyFindingDetail> => {
  const { data } = await apiClient.get<AnomalyFindingDetail>(
    `${base(workspaceId)}/${encodeURIComponent(findingId)}`,
  );
  return data;
};

export const getPriceSeries = async (
  workspaceId: string,
  vendorKey: string,
  sku: string,
): Promise<PriceSeries> => {
  const { data } = await apiClient.get<PriceSeries>(`${base(workspaceId)}/series`, {
    params: { vendor_key: vendorKey, sku },
  });
  return data;
};

export const confirmAnomaly = async (
  workspaceId: string,
  findingId: string,
  note?: string,
): Promise<AnomalyFindingDetail> => {
  const { data } = await apiClient.post<AnomalyFindingDetail>(
    `${base(workspaceId)}/${encodeURIComponent(findingId)}/confirm`,
    { note: note ?? null },
  );
  return data;
};

/**
 * Dismissal always carries a reason. The server refuses a blank one and so
 * does `ck_af_dismissal_has_reason`; this signature makes it impossible to
 * call without one rather than discovering it at 400.
 */
export const dismissAnomaly = async (
  workspaceId: string,
  findingId: string,
  reason: string,
  ttlDays: number | null = 365,
): Promise<AnomalyFindingDetail> => {
  const { data } = await apiClient.post<AnomalyFindingDetail>(
    `${base(workspaceId)}/${encodeURIComponent(findingId)}/dismiss`,
    { reason, ttl_days: ttlDays },
  );
  return data;
};

export const listSuppressions = async (
  workspaceId: string,
): Promise<AnomalySuppression[]> => {
  const { data } = await apiClient.get<AnomalySuppression[]>(
    `${base(workspaceId)}/suppressions`,
  );
  return data;
};
