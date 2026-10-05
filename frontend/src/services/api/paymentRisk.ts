/**
 * PHASE 4 — payment-risk flags on invoices (radar): a vendor's bank account
 * changed since its previous invoice (HIGH), or a suspiciously round total
 * (LOW). Accounts are only ever shown masked to their last four characters.
 */
import apiClient from "@/services/api/client";
import { PAYMENT_RISK_ENDPOINTS } from "@/services/api/endpoints";

export type PaymentRiskStatus = "OPEN" | "CONFIRMED" | "DISMISSED";

export interface PaymentRiskFlag {
  id: string;
  work_item_id: string;
  document: string | null;
  counterpart_work_item_id: string | null;
  kind: "BANK_ACCOUNT_CHANGED" | "ROUND_AMOUNT";
  severity: "LOW" | "MEDIUM" | "HIGH";
  status: PaymentRiskStatus;
  summary: string;
  details: Record<string, unknown>;
  review_note: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface PaymentRiskList {
  items: PaymentRiskFlag[];
  counts_by_status: Partial<Record<PaymentRiskStatus, number>>;
}

/** The server refuses a shorter dismissal reason (422). */
export const MIN_DISMISS_REASON = 10;

export const listPaymentRisk = async (
  workspaceId: string,
  status?: PaymentRiskStatus,
): Promise<PaymentRiskList> => {
  const response = await apiClient.get<PaymentRiskList>(
    PAYMENT_RISK_ENDPOINTS.list(workspaceId),
    status ? { params: { status } } : {},
  );
  return response.data;
};

export const confirmPaymentRisk = async (
  workspaceId: string,
  flagId: string,
): Promise<PaymentRiskFlag> =>
  (await apiClient.post<PaymentRiskFlag>(PAYMENT_RISK_ENDPOINTS.confirm(workspaceId, flagId), {})).data;

export const dismissPaymentRisk = async (
  workspaceId: string,
  flagId: string,
  reason: string,
): Promise<PaymentRiskFlag> =>
  (await apiClient.post<PaymentRiskFlag>(PAYMENT_RISK_ENDPOINTS.dismiss(workspaceId, flagId), { reason })).data;
