import apiClient from "@/services/api/client";
import type {
  BillingAccessResponse,
  SeatPriceBookResponse,
  CheckoutSessionRequest,
  EphemeralSessionResponse,
  InvoiceDetailResponse,
  InvoiceListResponse,
  InvoiceReproductionResponse,
  PlanListResponse,
  PortalSessionRequest,
  SeatSyncRequest,
  SubscriptionStateResponse,
  UsageGranularity,
  UsageLimitsResponse,
  UsagePeriod,
  UsageSeriesResponse,
  UsageSummaryResponse,
} from "@/types/billing";
import type { SpendLimit, SpendLimitUpdateRequest } from "@/types/usage";

const seg = (value: string): string => encodeURIComponent(value);

const org = (organizationId: string): string => {
  if (!organizationId) {
    throw new Error(
      "An organizationId is required to build this URL. Gate the query with `enabled: Boolean(organizationId)`.",
    );
  }
  return seg(organizationId);
};

const ws = (workspaceId: string): string => {
  if (!workspaceId) {
    throw new Error(
      "A workspaceId is required to build this URL. Gate the query with `enabled: Boolean(workspaceId)`.",
    );
  }
  return seg(workspaceId);
};

export const BILLING_ENDPOINTS = {
  plans: (organizationId: string) =>
    `/organizations/${org(organizationId)}/billing/plans`,
  subscription: (organizationId: string) =>
    `/organizations/${org(organizationId)}/billing/subscription`,
  access: (organizationId: string) =>
    `/organizations/${org(organizationId)}/billing/access`,
  checkoutSession: (organizationId: string) =>
    `/organizations/${org(organizationId)}/billing/checkout-session`,
  portalSession: (organizationId: string) =>
    `/organizations/${org(organizationId)}/billing/portal-session`,
  seats: (organizationId: string) =>
    `/organizations/${org(organizationId)}/billing/seats`,
  seatPriceBook: (organizationId: string) =>
    `/organizations/${org(organizationId)}/billing/price-book/seat`,

  invoices: (organizationId: string) =>
    `/organizations/${org(organizationId)}/invoices`,
  invoice: (organizationId: string, invoiceId: string) =>
    `/organizations/${org(organizationId)}/invoices/${seg(invoiceId)}`,
  invoiceReproduction: (organizationId: string, invoiceId: string) =>
    `/organizations/${org(organizationId)}/invoices/${seg(invoiceId)}/reproduction`,

  usageSummary: (organizationId: string) =>
    `/organizations/${org(organizationId)}/usage/summary`,
  usageSeries: (organizationId: string) =>
    `/organizations/${org(organizationId)}/usage/series`,
  usageLimits: (organizationId: string) =>
    `/organizations/${org(organizationId)}/usage/limits`,
  spendLimits: (organizationId: string) =>
    `/organizations/${org(organizationId)}/usage-limits`,

  workspaceUsageSummary: (workspaceId: string) =>
    `/workspaces/${ws(workspaceId)}/usage/summary`,
  workspaceUsageSeries: (workspaceId: string) =>
    `/workspaces/${ws(workspaceId)}/usage/series`,
} as const;

export const getPlans = async (
  organizationId: string,
): Promise<PlanListResponse> => {
  const response = await apiClient.get<PlanListResponse>(
    BILLING_ENDPOINTS.plans(organizationId),
  );
  return response.data;
};

export const getSubscriptionState = async (
  organizationId: string,
): Promise<SubscriptionStateResponse> => {
  const response = await apiClient.get<SubscriptionStateResponse>(
    BILLING_ENDPOINTS.subscription(organizationId),
  );
  return response.data;
};

export const getBillingAccess = async (
  organizationId: string,
): Promise<BillingAccessResponse> => {
  const response = await apiClient.get<BillingAccessResponse>(
    BILLING_ENDPOINTS.access(organizationId),
  );
  return response.data;
};

export const getUsageSummary = async (
  organizationId: string,
  params: { period?: UsagePeriod; at?: string } = {},
): Promise<UsageSummaryResponse> => {
  const response = await apiClient.get<UsageSummaryResponse>(
    BILLING_ENDPOINTS.usageSummary(organizationId),
    { params },
  );
  return response.data;
};

export const getUsageSeries = async (
  organizationId: string,
  params: {
    from: string;
    to?: string;
    granularity?: UsageGranularity;
  },
): Promise<UsageSeriesResponse> => {
  const response = await apiClient.get<UsageSeriesResponse>(
    BILLING_ENDPOINTS.usageSeries(organizationId),
    {
      params: {
        from: params.from,
        ...(params.to ? { to: params.to } : {}),
        ...(params.granularity ? { granularity: params.granularity } : {}),
      },
    },
  );
  return response.data;
};

export const getUsageLimits = async (
  organizationId: string,
): Promise<UsageLimitsResponse> => {
  const response = await apiClient.get<UsageLimitsResponse>(
    BILLING_ENDPOINTS.usageLimits(organizationId),
  );
  return response.data;
};

export const setSpendLimit = async (
  organizationId: string,
  data: SpendLimitUpdateRequest,
): Promise<SpendLimit> => {
  const response = await apiClient.put<SpendLimit>(
    BILLING_ENDPOINTS.spendLimits(organizationId),
    data,
  );
  return response.data;
};

export const getWorkspaceUsageSummary = async (
  workspaceId: string,
  params: { period?: UsagePeriod; at?: string } = {},
): Promise<UsageSummaryResponse> => {
  const response = await apiClient.get<UsageSummaryResponse>(
    BILLING_ENDPOINTS.workspaceUsageSummary(workspaceId),
    { params },
  );
  return response.data;
};

export const getWorkspaceUsageSeries = async (
  workspaceId: string,
  params: { from: string; to?: string; granularity?: UsageGranularity },
): Promise<UsageSeriesResponse> => {
  const response = await apiClient.get<UsageSeriesResponse>(
    BILLING_ENDPOINTS.workspaceUsageSeries(workspaceId),
    {
      params: {
        from: params.from,
        ...(params.to ? { to: params.to } : {}),
        ...(params.granularity ? { granularity: params.granularity } : {}),
      },
    },
  );
  return response.data;
};

export const getInvoices = async (
  organizationId: string,
): Promise<InvoiceListResponse> => {
  const response = await apiClient.get<InvoiceListResponse>(
    BILLING_ENDPOINTS.invoices(organizationId),
  );
  return response.data;
};

export const getInvoice = async (
  organizationId: string,
  invoiceId: string,
): Promise<InvoiceDetailResponse> => {
  const response = await apiClient.get<InvoiceDetailResponse>(
    BILLING_ENDPOINTS.invoice(organizationId, invoiceId),
  );
  return response.data;
};

export const getInvoiceReproduction = async (
  organizationId: string,
  invoiceId: string,
): Promise<InvoiceReproductionResponse> => {
  const response = await apiClient.get<InvoiceReproductionResponse>(
    BILLING_ENDPOINTS.invoiceReproduction(organizationId, invoiceId),
  );
  return response.data;
};

export const createCheckoutSession = async (
  organizationId: string,
  payload: CheckoutSessionRequest,
): Promise<EphemeralSessionResponse> => {
  const response = await apiClient.post<EphemeralSessionResponse>(
    BILLING_ENDPOINTS.checkoutSession(organizationId),
    payload,
  );
  return response.data;
};

export const createPortalSession = async (
  organizationId: string,
  payload: PortalSessionRequest = {},
): Promise<EphemeralSessionResponse> => {
  const response = await apiClient.post<EphemeralSessionResponse>(
    BILLING_ENDPOINTS.portalSession(organizationId),
    payload,
  );
  return response.data;
};

export const syncSeats = async (
  organizationId: string,
  payload: SeatSyncRequest = {},
): Promise<SubscriptionStateResponse> => {
  const response = await apiClient.post<SubscriptionStateResponse>(
    BILLING_ENDPOINTS.seats(organizationId),
    payload,
  );
  return response.data;
};

export const billingApi = {
  getPlans,
  getSubscriptionState,
  getBillingAccess,
  getUsageSummary,
  getUsageSeries,
  getUsageLimits,
  setSpendLimit,
  getWorkspaceUsageSummary,
  getWorkspaceUsageSeries,
  getInvoices,
  getInvoice,
  getInvoiceReproduction,
  createCheckoutSession,
  createPortalSession,
  syncSeats,
} as const;

export default billingApi;

/**
 * ARCH-24 Tranche 4 — what one more seat costs, straight from the backend.
 *
 * Returns 200 with `proration_micros: null` when Stripe is unreachable rather
 * than failing, so the caller must handle the null explicitly. That is the
 * point: an unknown proration is a real state the panel has to be able to
 * express, and a client that treats null as 0 shows a free seat.
 */
export const fetchSeatPriceBook = async (
  organizationId: string,
  additionalSeats = 1,
): Promise<SeatPriceBookResponse> => {
  const { data } = await apiClient.get<SeatPriceBookResponse>(
    BILLING_ENDPOINTS.seatPriceBook(organizationId),
    { params: { additional_seats: additionalSeats } },
  );
  return data;
};
