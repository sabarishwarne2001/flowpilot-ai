/**
 * ARCH50-S2:api-revops — the operator's RevOps console (superadmin) and the tenant's billing additions
 * (a promo quote, the invoiced contract).
 */
import { apiClient } from "@/services/api/client";
import type {
  Contract, ContractEndReason, ContractInput, ContractSummary, PriceBook, PriceBookSummary, PriceEntryInput,
  PromoCode, PromoCodeInput, PromoQuote, RevenueMetrics, RevOpsOrganization, SweepResult, TenantContract,
} from "@/types/revops";

const ADMIN = "/admin/revops";
const seg = (value: string): string => encodeURIComponent(value);

export const revopsKeys = {
  all: () => ["revops"] as const,
  metrics: () => ["revops", "metrics"] as const,
  books: () => ["revops", "books"] as const,
  book: (id: string) => ["revops", "books", id] as const,
  promos: () => ["revops", "promos"] as const,
  organizations: () => ["revops", "organizations"] as const,
  contracts: () => ["revops", "contracts"] as const,
  contract: (id: string) => ["revops", "contracts", id] as const,
  tenantContract: (organizationId: string) => ["billing", organizationId, "contract"] as const,
};

export const getRevenueMetrics = async (): Promise<RevenueMetrics> =>
  (await apiClient.get<RevenueMetrics>(`${ADMIN}/metrics`)).data;
export const runRevOpsSweep = async (): Promise<SweepResult> =>
  (await apiClient.post<SweepResult>(`${ADMIN}/sweep`, {})).data;

export const listPriceBooks = async (): Promise<readonly PriceBookSummary[]> =>
  (await apiClient.get<PriceBookSummary[]>(`${ADMIN}/price-books`)).data;
export const createPriceBook = async (code: string, currency: string, notes: string | null): Promise<PriceBook> =>
  (await apiClient.post<PriceBook>(`${ADMIN}/price-books`, { code, currency, notes })).data;
export const getPriceBook = async (id: string): Promise<PriceBook> =>
  (await apiClient.get<PriceBook>(`${ADMIN}/price-books/${seg(id)}`)).data;
export const setPriceEntry = async (id: string, entry: PriceEntryInput): Promise<PriceBook> =>
  (await apiClient.put<PriceBook>(`${ADMIN}/price-books/${seg(id)}/entries`, entry)).data;
export const deletePriceEntry = async (id: string, entryId: string): Promise<PriceBook> =>
  (await apiClient.delete<PriceBook>(`${ADMIN}/price-books/${seg(id)}/entries/${seg(entryId)}`)).data;
export const publishPriceBook = async (id: string): Promise<PriceBook> =>
  (await apiClient.post<PriceBook>(`${ADMIN}/price-books/${seg(id)}/publish`, {})).data;

export const listPromoCodes = async (): Promise<readonly PromoCode[]> =>
  (await apiClient.get<PromoCode[]>(`${ADMIN}/promo-codes`)).data;
export const createPromoCode = async (input: PromoCodeInput): Promise<PromoCode> =>
  (await apiClient.post<PromoCode>(`${ADMIN}/promo-codes`, input)).data;
export const setPromoActive = async (id: string, isActive: boolean): Promise<PromoCode> =>
  (await apiClient.put<PromoCode>(`${ADMIN}/promo-codes/${seg(id)}/active`, { is_active: isActive })).data;

export const listRevOpsOrganizations = async (): Promise<readonly RevOpsOrganization[]> =>
  (await apiClient.get<RevOpsOrganization[]>(`${ADMIN}/organizations`)).data;
export const listContracts = async (): Promise<readonly ContractSummary[]> =>
  (await apiClient.get<ContractSummary[]>(`${ADMIN}/contracts`)).data;
export const createContract = async (input: ContractInput): Promise<Contract> =>
  (await apiClient.post<Contract>(`${ADMIN}/contracts`, input)).data;
export const getContract = async (id: string): Promise<Contract> =>
  (await apiClient.get<Contract>(`${ADMIN}/contracts/${seg(id)}`)).data;
export const activateContract = async (id: string): Promise<Contract> =>
  (await apiClient.post<Contract>(`${ADMIN}/contracts/${seg(id)}/activate`, {})).data;
export const issueContractInvoices = async (id: string): Promise<Contract> =>
  (await apiClient.post<Contract>(`${ADMIN}/contracts/${seg(id)}/issue`, {})).data;
export const endContract = async (id: string, reason: ContractEndReason): Promise<Contract> =>
  (await apiClient.post<Contract>(`${ADMIN}/contracts/${seg(id)}/end`, { reason })).data;
export const payInvoice = async (id: string, reference: string): Promise<Contract> =>
  (await apiClient.post<Contract>(`${ADMIN}/invoices/${seg(id)}/pay`, { reference })).data;
export const voidInvoice = async (id: string, reason: string): Promise<Contract> =>
  (await apiClient.post<Contract>(`${ADMIN}/invoices/${seg(id)}/void`, { reason })).data;

export const quotePromoCode = async (organizationId: string, body: {
  readonly code: string; readonly quota_tier_key: string; readonly interval: string; readonly currency: string;
  readonly seats: number;
}): Promise<PromoQuote> =>
  (await apiClient.post<PromoQuote>(`/organizations/${seg(organizationId)}/billing/promo-quote`, body)).data;

export const getTenantContract = async (organizationId: string): Promise<TenantContract> =>
  (await apiClient.get<TenantContract>(`/organizations/${seg(organizationId)}/billing/contract`)).data;
