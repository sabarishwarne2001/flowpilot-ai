/**
 * Billing, usage, and invoice DTOs.
 * Mirrors `app/schemas/usage.py` and `app/schemas/invoice.py`.
 */

export type UsagePeriod = "DAY" | "MONTH";

export type UsageGranularity = "HOUR" | "DAY" | "MONTH";

export interface UsageLine {
  readonly event_type: string;
  readonly unit: string;
  readonly quantity: string;
  readonly estimated_quantity: string;
  readonly cost_micros: number;
  readonly estimated_cost_micros: number;
  readonly event_count: number;
  readonly late_quantity: string;
  readonly late_cost_micros: number;
}

export interface UsageSummaryResponse {
  readonly organization_id: string;
  readonly workspace_id: string | null;
  readonly period: UsagePeriod;
  readonly period_start: string;
  readonly period_end: string;
  readonly currency: string;
  readonly sealed: boolean;
  readonly sealed_at: string | null;
  readonly as_of: string | null;
  readonly lines: readonly UsageLine[];
  readonly total_cost_micros: number;
  readonly estimated_cost_micros: number;
  readonly late_cost_micros: number;
}

export interface UsageBucket {
  readonly bucket_start: string;
  readonly bucket_end: string;
  readonly sealed: boolean;
  readonly lines: readonly UsageLine[];
  readonly total_cost_micros: number;
  readonly estimated_cost_micros: number;
}

export interface UsageSeriesResponse {
  readonly organization_id: string;
  readonly workspace_id: string | null;
  readonly granularity: UsageGranularity;
  readonly range_start: string;
  readonly range_end: string;
  readonly currency: string;
  readonly buckets: readonly UsageBucket[];
  readonly total_cost_micros: number;
  readonly estimated_cost_micros: number;
}

export interface UsageLimit {
  readonly limit_key: string;
  readonly period: string;
  readonly source: string;
  readonly max_quantity: string | null;
  readonly max_cost_micros: number | null;
  readonly current_quantity: string;
  readonly current_cost_micros: number;
  readonly remaining_quantity: string | null;
  readonly remaining_cost_micros: number | null;
  readonly overage_policy: string;
  readonly grace_quantity: string | null;
  readonly hard_stop: boolean;
  readonly quota_tier_key: string | null;
  readonly quota_tier_version: number | null;
  readonly period_start: string;
  readonly resets_at: string;
}

export interface UsageLimitsResponse {
  readonly organization_id: string;
  readonly quota_tier_key: string | null;
  readonly quota_tier_version: number | null;
  readonly quota_tier_display_name: string | null;
  readonly as_of: string;
  readonly limits: readonly UsageLimit[];
}

export interface InvoiceLineItemRead {
  readonly line_number: number;
  readonly kind: string;
  readonly description: string;
  readonly quantity: string;
  readonly unit: string;
  readonly unit_price_micros: string;
  readonly amount_micros: number;
  readonly limit_key: string | null;
  readonly event_type: string | null;
  readonly included_quantity: string | null;
  readonly estimated_quantity: string | null;
  readonly usage_event_count: number | null;
  readonly price_book_entry_id: string | null;
}

export interface InvoiceSummary {
  readonly id: string;
  readonly number: string;
  readonly status: string;
  readonly currency: string;
  readonly period_start: string;
  readonly period_end: string;
  readonly subtotal_micros: number;
  readonly tax_micros: number;
  readonly total_micros: number;
  readonly amount_paid_micros: number;
  readonly seats_billed: number;
  readonly finalized_at: string | null;
  readonly stripe_invoice_id: string | null;
}

export interface InvoiceListResponse {
  readonly organization_id: string;
  readonly invoices: readonly InvoiceSummary[];
  readonly count: number;
}

export interface InvoiceDetailResponse {
  readonly invoice: InvoiceSummary;
  readonly line_items: readonly InvoiceLineItemRead[];
  readonly content_digest: string;
  readonly digest_matches: boolean;
  readonly assembly_notes: Record<string, unknown> | null;
}

export interface InvoiceProvenance {
  readonly price_book_id: string;
  readonly price_book_version: number;
  readonly price_book_currency: string;
  readonly quota_tier_id: string;
  readonly quota_tier_key: string;
  readonly quota_tier_version: number;
}

export interface InvoiceIntegrity {
  readonly digest_matches: boolean;
  readonly stored_digest: string;
  readonly recomputed_digest: string;
  readonly arithmetic_ok: boolean;
  readonly arithmetic_failures: readonly Record<string, unknown>[];
  readonly reproducible: boolean;
}

export interface InvoiceReproductionResponse {
  readonly invoice: Record<string, unknown>;
  readonly provenance: InvoiceProvenance;
  readonly integrity: InvoiceIntegrity;
  readonly lines: readonly Record<string, unknown>[];
}

export interface SubscriptionBrief {
  readonly id: string;
  readonly stripe_subscription_id: string;
  readonly status: string;
  readonly quota_tier_key: string;
  readonly quota_tier_id: string;
  readonly price_book_id: string;
  readonly current_period_start: string;
  readonly current_period_end: string;
  readonly cancel_at_period_end: boolean;
}

export interface SubscriptionStateResponse {
  readonly organization_id: string;
  readonly has_billing_account: boolean;
  readonly currency: string | null;
  readonly billing_email: string | null;
  readonly delinquent_since: string | null;
  readonly subscription: SubscriptionBrief | null;
  readonly seats_billable: number;
  readonly seats_purchased: number;
  readonly seat_drift_delta: number;
  readonly access_state: string;
}

export interface BillingAccessResponse {
  readonly organization_id: string;
  readonly access_state: string;
  readonly writes_allowed: boolean;
  readonly reads_allowed: boolean;
  readonly export_allowed: boolean;
  readonly data_retained: boolean;
  readonly dunning_steps_applied: readonly string[];
  readonly next_dunning_step: string | null;
}

export interface CheckoutSessionRequest {
  readonly quota_tier_key: string;
  readonly seats?: number;
  readonly price_id?: string;
  readonly success_url?: string;
  readonly cancel_url?: string;
}

export interface PortalSessionRequest {
  readonly return_url?: string;
}

export interface SeatSyncRequest {
  readonly reason?: string;
  readonly force?: boolean;
}

export interface EphemeralSessionResponse {
  readonly url: string;
  readonly expires_at?: string | null;
}

// --- Plan List DTOs ---

export interface PlanEntitlement {
  readonly event_type: string;
  readonly limit_quantity: number | null;
  readonly limit_cost_micros: number | null;
  readonly overage_policy: string;
  readonly period: string;
}

export interface PlanOption {
  readonly key: string;
  readonly display_name: string;
  readonly version: number;
  readonly is_current: boolean;
  readonly price_id: string | null;
  readonly unit_amount: number | null;
  readonly currency: string | null;
  readonly interval: string | null;
  readonly entitlements: readonly PlanEntitlement[];
  readonly notes: string | null;
}

export interface PlanListResponse {
  readonly organization_id: string;
  readonly current_tier_key: string | null;
  readonly as_of: string;
  readonly plans: readonly PlanOption[];
}

export const microsToUnits = (micros: number): number => micros / 1_000_000;

export const formatMicros = (
  micros: number,
  currency: string = "USD",
  locale?: string,
): string => {
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: currency.toUpperCase(),
    }).format(microsToUnits(micros));
  } catch {
    return `${microsToUnits(micros).toFixed(2)} ${currency.toUpperCase()}`;
  }
};

export const parseQuantity = (value: string | null | undefined): number => {
  if (!value) {
    return 0;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

export const limitUtilisation = (limit: UsageLimit): number | null => {
  if (limit.max_cost_micros !== null && limit.max_cost_micros > 0) {
    return limit.current_cost_micros / limit.max_cost_micros;
  }
  if (limit.max_quantity !== null) {
    const max = parseQuantity(limit.max_quantity);
    if (max > 0) {
      return parseQuantity(limit.current_quantity) / max;
    }
  }
  return null;
};
