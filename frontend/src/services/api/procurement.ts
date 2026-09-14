/** ARCH-31 Step 4 — procurement matching API client. */

import { apiClient } from "@/services/api/client";
import type {
  CaseDetail,
  CaseStatus,
  CaseSummary,
  ImpactPreview,
  TolerancePolicy,
} from "@/types/procurement";

const base = (workspaceId: string): string =>
  `/workspaces/${encodeURIComponent(workspaceId)}/procurement`;

export interface CaseQueueFilters {
  readonly status?: readonly CaseStatus[];
  readonly hasExceptions?: boolean;
  readonly minVarianceMicros?: number;
  readonly limit?: number;
  readonly offset?: number;
}

export const listCases = async (
  workspaceId: string,
  filters: CaseQueueFilters = {},
): Promise<CaseSummary[]> => {
  const params = new URLSearchParams();
  filters.status?.forEach((value) => params.append("status", value));
  if (filters.hasExceptions !== undefined) {
    params.set("has_exceptions", String(filters.hasExceptions));
  }
  if (filters.minVarianceMicros !== undefined) {
    params.set("min_variance_micros", String(filters.minVarianceMicros));
  }
  params.set("limit", String(filters.limit ?? 50));
  params.set("offset", String(filters.offset ?? 0));

  const { data } = await apiClient.get<CaseSummary[]>(
    `${base(workspaceId)}/cases?${params.toString()}`,
  );
  return data;
};

export const getCase = async (
  workspaceId: string,
  caseId: string,
): Promise<CaseDetail> => {
  const { data } = await apiClient.get<CaseDetail>(
    `${base(workspaceId)}/cases/${encodeURIComponent(caseId)}`,
  );
  return data;
};

export const approveCase = async (
  workspaceId: string,
  caseId: string,
  overrideReason?: string,
): Promise<CaseDetail> => {
  const { data } = await apiClient.post<CaseDetail>(
    `${base(workspaceId)}/cases/${encodeURIComponent(caseId)}/approve`,
    { override_reason: overrideReason ?? null },
  );
  return data;
};

export const disputeCase = async (
  workspaceId: string,
  caseId: string,
  reason: string,
): Promise<CaseDetail> => {
  const { data } = await apiClient.post<CaseDetail>(
    `${base(workspaceId)}/cases/${encodeURIComponent(caseId)}/dispute`,
    { reason },
  );
  return data;
};

export const rematchCase = async (
  workspaceId: string,
  caseId: string,
  counterparts: {
    readonly poWorkItemId?: string | null;
    readonly receiptWorkItemId?: string | null;
  },
): Promise<{ job_id: string; case_id: string }> => {
  const { data } = await apiClient.post<{ job_id: string; case_id: string }>(
    `${base(workspaceId)}/cases/${encodeURIComponent(caseId)}/rematch`,
    {
      po_work_item_id: counterparts.poWorkItemId ?? null,
      receipt_work_item_id: counterparts.receiptWorkItemId ?? null,
    },
  );
  return data;
};

export const listPolicies = async (
  workspaceId: string,
): Promise<TolerancePolicy[]> => {
  const { data } = await apiClient.get<TolerancePolicy[]>(
    `${base(workspaceId)}/policies`,
  );
  return data;
};

export interface PolicyDraft {
  readonly price_tolerance_micros: number;
  readonly price_tolerance_bps: number;
  readonly quantity_tolerance: string;
  readonly max_pair_cost: number;
  readonly candidate_window_days: number;
}

export const publishPolicy = async (
  workspaceId: string,
  draft: PolicyDraft,
): Promise<TolerancePolicy> => {
  const { data } = await apiClient.post<TolerancePolicy>(
    `${base(workspaceId)}/policies`,
    draft,
  );
  return data;
};

export const previewPolicy = async (
  workspaceId: string,
  draft: PolicyDraft,
  windowDays = 30,
): Promise<ImpactPreview> => {
  const { data } = await apiClient.post<ImpactPreview>(
    `${base(workspaceId)}/policies/preview`,
    { ...draft, window_days: windowDays },
  );
  return data;
};

export const overrideDocumentRole = async (
  workspaceId: string,
  workItemId: string,
  role: string,
): Promise<{ role: string; role_source: string; previous_role: string }> => {
  const { data } = await apiClient.put<{
    role: string;
    role_source: string;
    previous_role: string;
  }>(`${base(workspaceId)}/roles/${encodeURIComponent(workItemId)}`, { role });
  return data;
};
