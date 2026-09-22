/**
 * HARDENING-T2:D9 — place and release legal holds.
 *
 * Kept out of services/api/ingestion.ts on purpose: ARCH-38 created that file
 * and its idempotency gate compares it byte-for-byte.
 */
import { apiClient } from "@/services/api/client";
import { WORK_ITEM_ENDPOINTS } from "@/services/api/endpoints";
import type { RetentionHold } from "@/services/api/ingestion";

const JSON_HEADERS = { "Content-Type": "application/json", Accept: "application/json" } as const;

export const placeRetentionHold = async (
  workspaceId: string,
  body: { reason: string; reference?: string | null; work_item_id?: string | null },
): Promise<RetentionHold> => {
  const response = await apiClient.post<RetentionHold>(WORK_ITEM_ENDPOINTS.retentionHolds(workspaceId), body, {
    headers: JSON_HEADERS,
  });
  return response.data;
};

export const releaseRetentionHold = async (workspaceId: string, holdId: string): Promise<RetentionHold> => {
  const response = await apiClient.delete<RetentionHold>(WORK_ITEM_ENDPOINTS.retentionHold(workspaceId, holdId), {
    headers: JSON_HEADERS,
  });
  return response.data;
};
