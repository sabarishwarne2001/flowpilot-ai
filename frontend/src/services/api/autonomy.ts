/** ARCH-35 — calibrated autonomy API client. */

import { apiClient } from "@/services/api/client";
import { AUTONOMY_ENDPOINTS } from "@/services/api/endpoints";
import type {
  AutonomyActionResult,
  AutonomyOverview,
  AutonomyReliability,
  AutonomySettingsRequest,
} from "@/types/autonomy";

export const getAutonomyOverview = async (
  organizationId: string,
): Promise<AutonomyOverview> => {
  const { data } = await apiClient.get<AutonomyOverview>(
    AUTONOMY_ENDPOINTS.overview(organizationId),
  );
  return data;
};

export const getAutonomyReliability = async (
  organizationId: string,
  decisionType: string,
): Promise<AutonomyReliability> => {
  const { data } = await apiClient.get<AutonomyReliability>(
    AUTONOMY_ENDPOINTS.reliability(organizationId, decisionType),
  );
  return data;
};

/**
 * Slider positions are integers — tenths of a percent for α, whole percents
 * for the audit share — converted to fixed-precision Decimal strings exactly
 * once, here. 0.05 has no exact binary float, and the server compares it.
 */
export const alphaFromTenths = (tenthsOfPercent: number): string =>
  (tenthsOfPercent / 1000).toFixed(5);

export const auditRateFromPercent = (percent: number): string =>
  (percent / 100).toFixed(4);

export const updateAutonomySettings = async (
  organizationId: string,
  decisionType: string,
  body: AutonomySettingsRequest,
): Promise<AutonomyActionResult> => {
  const { data } = await apiClient.put<AutonomyActionResult>(
    AUTONOMY_ENDPOINTS.settings(organizationId, decisionType),
    body,
  );
  return data;
};

export const resumeAutonomy = async (
  organizationId: string,
  decisionType: string,
): Promise<AutonomyActionResult> => {
  const { data } = await apiClient.post<AutonomyActionResult>(
    AUTONOMY_ENDPOINTS.resume(organizationId, decisionType),
  );
  return data;
};
