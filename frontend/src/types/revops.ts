/**
 * ARCH50-S2:types-revops — plan price books (annual, INR), promo codes, invoiced enterprise contracts and revenue
 * metrics. Mirrors app/schemas/revops.py and app/services/revops/vocabulary.py (verify_arch50 W3 proves the parity).
 */

export const CURRENCIES = ["USD", "INR"] as const;
export type Currency = (typeof CURRENCIES)[number];
export const PLAN_INTERVALS = ["month", "year"] as const;
export type PlanInterval = (typeof PLAN_INTERVALS)[number];
export const CONTRACT_INTERVALS = ["month", "quarter", "year"] as const;
export type ContractInterval = (typeof CONTRACT_INTERVALS)[number];
export const BOOK_STATUSES = ["DRAFT", "PUBLISHED", "RETIRED"] as const;
export const PROMO_DURATIONS = ["ONCE", "REPEATING", "FOREVER"] as const;
export type PromoDuration = (typeof PROMO_DURATIONS)[number];
export const CONTRACT_STATUSES = ["DRAFT", "ACTIVE", "ENDED", "CANCELLED"] as const;
export const CONTRACT_END_REASONS = ["TERM_ENDED", "CANCELLED", "SUPERSEDED", "NON_PAYMENT"] as const;
export type ContractEndReason = (typeof CONTRACT_END_REASONS)[number];
export const INVOICE_STATUSES = ["ISSUED", "PAID", "VOID"] as const;

export const REVOPS_CODES = [
  "BOOK_NOT_DRAFT", "BOOK_EMPTY", "BOOK_UNKNOWN_TIER", "BOOK_CODE_TAKEN", "PRICE_NOT_SOLD",
  "PROMO_UNKNOWN", "PROMO_INACTIVE", "PROMO_EXPIRED", "PROMO_EXHAUSTED", "PROMO_NOT_APPLICABLE",
  "PROMO_CURRENCY_MISMATCH", "PROMO_FIRST_SUBSCRIPTION_ONLY", "PROMO_ALREADY_USED", "PROMO_NOT_AVAILABLE_ONLINE",
  "PROMO_CODE_TAKEN", "CONTRACT_NOT_DRAFT", "CONTRACT_NOT_ACTIVE", "CONTRACT_ACTIVE_EXISTS",
  "SUBSCRIPTION_ACTIVE", "CONTRACT_NUMBER_TAKEN", "CONTRACT_TERM_TOO_LONG", "TIER_NOT_PUBLISHED",
  "INVOICE_NOT_ISSUED", "NOT_FOUND",
] as const;
export type RevOpsCode = (typeof REVOPS_CODES)[number];

export interface PriceEntry {
  readonly id: string;
  readonly tier_key: string;
  readonly billing_interval: string;
  readonly unit_amount_micros: number;
  readonly gateway_price_id: string | null;
}

export interface PriceBook {
  readonly id: string;
  readonly code: string;
  readonly currency: string;
  readonly status: string;
  readonly notes: string | null;
  readonly content_digest: string | null;
  readonly published_at: string | null;
  readonly retired_at: string | null;
  readonly created_at: string;
  readonly entries: readonly PriceEntry[];
}

export interface PriceBookSummary {
  readonly id: string;
  readonly code: string;
  readonly currency: string;
  readonly status: string;
  readonly notes: string | null;
  readonly content_digest: string | null;
  readonly published_at: string | null;
  readonly retired_at: string | null;
  readonly created_at: string;
  readonly entries: number;
}

export interface PriceEntryInput {
  readonly tier_key: string;
  readonly interval: PlanInterval;
  readonly unit_amount: number;
  readonly gateway_price_id: string | null;
}

export interface PromoCode {
  readonly id: string;
  readonly code: string;
  readonly description: string | null;
  readonly percent_off: number | null;
  readonly amount_off_micros: number | null;
  readonly currency: string | null;
  readonly duration: string;
  readonly duration_in_months: number | null;
  readonly max_redemptions: number | null;
  readonly times_redeemed: number;
  readonly redeem_by: string | null;
  readonly applies_to_tiers: readonly string[] | null;
  readonly applies_to_intervals: readonly string[] | null;
  readonly first_subscription_only: boolean;
  readonly gateway_coupon_id: string | null;
  readonly is_active: boolean;
  readonly created_at: string;
  readonly redemptions: Record<string, number>;
}

export interface PromoCodeInput {
  readonly code: string;
  readonly description?: string | undefined;
  readonly percent_off?: number | undefined;
  readonly amount_off?: number | undefined;
  readonly currency?: Currency | undefined;
  readonly duration: PromoDuration;
  readonly duration_in_months?: number | undefined;
  readonly max_redemptions?: number | undefined;
  readonly redeem_by?: string | undefined;
  readonly applies_to_tiers?: readonly string[] | undefined;
  readonly applies_to_intervals?: readonly PlanInterval[] | undefined;
  readonly first_subscription_only?: boolean | undefined;
  readonly gateway_coupon_id?: string | undefined;
}

export interface ContractInvoice {
  readonly id: string;
  readonly invoice_number: string;
  readonly period_start: string;
  readonly period_end: string;
  readonly currency: string;
  readonly subtotal_micros: number;
  readonly discount_micros: number;
  readonly tax_micros: number;
  readonly total_micros: number;
  readonly status: string;
  readonly issued_at: string;
  readonly due_at: string;
  readonly paid_at: string | null;
  readonly payment_reference: string | null;
  readonly void_reason: string | null;
  readonly overdue: boolean;
}

export interface Contract {
  readonly id: string;
  readonly organization_id: string;
  readonly organization_name: string | null;
  readonly contract_number: string;
  readonly tier_key: string;
  readonly seats: number;
  readonly currency: string;
  readonly billing_interval: string;
  readonly amount_per_period_micros: number;
  readonly tax_rate_bps: number;
  readonly term_start: string;
  readonly term_end: string;
  readonly payment_terms_days: number;
  readonly po_number: string | null;
  readonly billing_email: string | null;
  readonly status: string;
  readonly promo_code_id: string | null;
  readonly activated_at: string | null;
  readonly ended_at: string | null;
  readonly end_reason: string | null;
  readonly notes: string | null;
  readonly created_at: string;
  readonly invoices: readonly ContractInvoice[];
}

/** An organization a contract can be drafted for (GET /admin/revops/organizations). */
export interface RevOpsOrganization {
  readonly id: string;
  readonly name: string;
  readonly slug: string;
  readonly status: string;
  readonly tier_key: string | null;
  readonly has_active_contract: boolean;
}

export interface ContractSummary {
  readonly id: string;
  readonly organization_id: string;
  readonly organization_name: string | null;
  readonly contract_number: string;
  readonly tier_key: string;
  readonly seats: number;
  readonly currency: string;
  readonly billing_interval: string;
  readonly amount_per_period_micros: number;
  readonly term_start: string;
  readonly term_end: string;
  readonly status: string;
  readonly open_invoices: number;
  readonly overdue_invoices: number;
  readonly created_at: string;
}

export interface ContractInput {
  readonly organization_id: string;
  readonly contract_number: string;
  readonly tier_key: string;
  readonly seats: number;
  readonly currency: Currency;
  readonly billing_interval: ContractInterval;
  readonly amount_per_period: number;
  readonly tax_rate_bps: number;
  readonly term_start: string;
  readonly term_end: string;
  readonly payment_terms_days: number;
  readonly po_number?: string | undefined;
  readonly billing_email?: string | undefined;
  readonly promo_code?: string | undefined;
  readonly notes?: string | undefined;
}

export interface CurrencyMetrics {
  readonly mrr_micros: number;
  readonly arr_micros: number;
  readonly customers: number;
  readonly contract_customers: number;
  readonly arpa_micros: number;
  readonly movements: { readonly new: number; readonly expansion: number; readonly contraction: number; readonly churned: number };
}

export interface RevenueMetrics {
  readonly as_of: string;
  readonly compare_days: number;
  readonly currencies: Record<string, CurrencyMetrics>;
  readonly receivables: Record<string, { readonly outstanding_micros: number; readonly overdue_micros: number; readonly overdue_invoices: number }>;
  readonly unpriced_subscriptions: readonly Record<string, unknown>[];
  readonly promo_redemptions: Record<string, { readonly count: number; readonly discount_micros: number }>;
  readonly snapshots: readonly Record<string, unknown>[];
}

export interface SweepResult {
  readonly reservations_expired: number;
  readonly redemptions_confirmed: number;
  readonly contracts_ended: number;
  readonly invoices_issued: number;
  readonly snapshots_written: number;
}

export interface PromoQuote {
  readonly code: string;
  readonly valid: boolean;
  readonly duration: string;
  readonly duration_in_months: number | null;
  readonly list_amount_micros: number;
  readonly discount_micros: number;
  readonly final_amount_micros: number;
  readonly online: boolean;
}

export interface TenantContract {
  readonly contract: Contract | null;
}

const MONEY_CACHE = new Map<string, Intl.NumberFormat>();

/** Micros in a currency → "₹1,50,000.00" / "$49.00" (the viewer's locale). */
export const money = (micros: number | null | undefined, currency: string | null | undefined): string => {
  if (micros === null || micros === undefined || !currency) {
    return "—";
  }
  const code = currency.toUpperCase();
  let format = MONEY_CACHE.get(code);
  if (!format) {
    format = new Intl.NumberFormat(undefined, { style: "currency", currency: code });
    MONEY_CACHE.set(code, format);
  }
  return format.format(micros / 1_000_000);
};

export const REVOPS_MESSAGES: Partial<Record<RevOpsCode, string>> = {
  PROMO_UNKNOWN: "That promo code doesn't exist.",
  PROMO_INACTIVE: "That promo code is no longer active.",
  PROMO_EXPIRED: "That promo code has expired.",
  PROMO_EXHAUSTED: "That promo code has been fully redeemed.",
  PROMO_NOT_APPLICABLE: "That promo code doesn't apply to this plan or billing interval.",
  PROMO_CURRENCY_MISMATCH: "That promo code is for another currency.",
  PROMO_FIRST_SUBSCRIPTION_ONLY: "That promo code is for a first subscription only.",
  PROMO_ALREADY_USED: "Your organization has already used that code.",
  PROMO_NOT_AVAILABLE_ONLINE: "That code applies to invoiced contracts only — contact sales.",
  PRICE_NOT_SOLD: "This plan isn't sold self-serve in that currency or interval.",
  SUBSCRIPTION_ACTIVE: "The organization has a live card subscription; cancel it before activating a contract.",
  CONTRACT_ACTIVE_EXISTS: "The organization already has an active contract.",
};
