import apiClient from "@/services/api/client";
import { COGS_ENDPOINTS } from "@/services/api/endpoints";
import type {
  AcceptVarianceRequest,
  MarginOrder,
  PlatformMarginSummary,
  ProviderCostResponse,
  RateCardResponse,
  ReconcileRequest,
  SupplierInvoice,
  SupplierInvoiceCreateRequest,
  SupplierInvoiceListResponse,
  SupplierReconciliation,
  TenantEconomicsResponse,
} from "@/types/cogs";

/**
 * A reporting window, as the API expects it.
 *
 * The margin endpoints take exclusive upper bounds; `supplier_invoices`
 * takes an inclusive `period_end` date because that is how a supplier writes
 * an invoice. Two conventions in one feature is a genuine cost, and the
 * alternative — transcribing "31 Jul" as "1 Aug" by hand every month — is a
 * transcription error against a financial figure.
 */
export interface MarginWindow {
  readonly periodStart: Date;
  readonly periodEnd: Date;
}

const windowParams = (
  window: MarginWindow,
): Record<string, string> => ({
  period_start: window.periodStart.toISOString(),
  period_end: window.periodEnd.toISOString(),
});

/** Stable cache-key fragment for a window. Seconds are noise here. */
export const windowKey = (window: MarginWindow): string =>
  `${window.periodStart.toISOString().slice(0, 10)}..${window.periodEnd
    .toISOString()
    .slice(0, 10)}`;

export const trailingWindow = (days: number): MarginWindow => {
  const periodEnd = new Date();
  const periodStart = new Date(periodEnd.getTime() - days * 86_400_000);
  return { periodStart, periodEnd };
};

/* ---------------------------------------------------------------------- */

export const getMarginSummary = async (
  window: MarginWindow,
): Promise<PlatformMarginSummary> => {
  const response = await apiClient.get<PlatformMarginSummary>(
    COGS_ENDPOINTS.marginSummary(),
    { params: windowParams(window), headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const getTenantEconomics = async (
  window: MarginWindow,
  order: MarginOrder = "MARGIN_ASC",
  limit = 50,
): Promise<TenantEconomicsResponse> => {
  const response = await apiClient.get<TenantEconomicsResponse>(
    COGS_ENDPOINTS.tenantEconomics(),
    {
      params: { ...windowParams(window), order, limit },
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

export const getProviderCosts = async (
  window: MarginWindow,
): Promise<ProviderCostResponse> => {
  const response = await apiClient.get<ProviderCostResponse>(
    COGS_ENDPOINTS.providerCosts(),
    { params: windowParams(window), headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const getRateCard = async (): Promise<RateCardResponse> => {
  const response = await apiClient.get<RateCardResponse>(
    COGS_ENDPOINTS.rateCard(),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const listSupplierInvoices = async (
  provider?: string,
): Promise<SupplierInvoiceListResponse> => {
  const response = await apiClient.get<SupplierInvoiceListResponse>(
    COGS_ENDPOINTS.supplierInvoices(),
    {
      params: provider ? { provider } : undefined,
      headers: { Accept: "application/json" },
    },
  );
  return response.data;
};

export const createSupplierInvoice = async (
  payload: SupplierInvoiceCreateRequest,
): Promise<SupplierInvoice> => {
  const response = await apiClient.post<SupplierInvoice>(
    COGS_ENDPOINTS.supplierInvoices(),
    payload,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const reconcileSupplierInvoice = async (
  supplierInvoiceId: string,
  payload: ReconcileRequest = {},
): Promise<SupplierReconciliation> => {
  const response = await apiClient.post<SupplierReconciliation>(
    COGS_ENDPOINTS.reconcileInvoice(supplierInvoiceId),
    payload,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const listInvoiceReconciliations = async (
  supplierInvoiceId: string,
): Promise<readonly SupplierReconciliation[]> => {
  const response = await apiClient.get<SupplierReconciliation[]>(
    COGS_ENDPOINTS.invoiceReconciliations(supplierInvoiceId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const acceptVariance = async (
  reconciliationId: string,
  payload: AcceptVarianceRequest,
): Promise<SupplierReconciliation> => {
  const response = await apiClient.post<SupplierReconciliation>(
    COGS_ENDPOINTS.acceptVariance(reconciliationId),
    payload,
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

/**
 * Currency units to integer micros, for the invoice form.
 *
 * Rounds rather than truncates, and goes through a string to avoid the
 * float artefact that makes `19.99 * 1_000_000` land on 19989999.999999998.
 */
export const toMicros = (amount: string | number): number => {
  const value = typeof amount === "number" ? amount : Number.parseFloat(amount);
  if (!Number.isFinite(value)) {
    return Number.NaN;
  }
  return Math.round(value * 1_000_000);
};
