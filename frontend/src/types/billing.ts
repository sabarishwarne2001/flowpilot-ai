import { formatMoney } from "@/utils/displayTime";
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
  /** Campaign session 4 (N-069): how much of the payment the gateway has refunded. */
  readonly amount_refunded_micros?: number;
  readonly refunded_at?: string | null;
  readonly seats_billed: number;
  readonly finalized_at: string | null;
  readonly stripe_invoice_id: string | null;
  /** Website and payments session (C4): the gateway that took the payment. */
  readonly gateway?: "STRIPE" | "DODO";
  /** Who issued the tax invoice ("Dodo Payments (merchant of record)"), when not Servabo. */
  readonly issued_by?: string | null;
  /** Whether "Download invoice" fetches the gateway's PDF. */
  readonly downloadable?: boolean;
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
  /** Null for subscriptions issued by a gateway other than Stripe. */
  readonly stripe_subscription_id: string | null;
  readonly gateway: string;
  readonly gateway_subscription_id: string | null;
  readonly grace_ends_at: string | null;
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
  /** Campaign session 1: what the server's seat check enforces. */
  readonly plan_key: string | null;
  /** The plan's seats on Free, the purchased quantity on a paid plan; null = no limit. */
  readonly seat_capacity: number | null;
  readonly seat_capacity_source: "PURCHASED" | "PLAN" | "OVERRIDE" | "UNLIMITED";
  readonly seats_pending_invitations: number;
  /** Active members plus pending invitations. */
  readonly seats_used: number;
  readonly seats_available: number | null;
  /** The caller may buy or release seats (owner, admin, billing on a paid plan). */
  readonly can_manage_seats: boolean;
}

export interface SeatChangeRequest {
  readonly seats: number;
  /** The per-seat price that was shown; any other price is refused (SEAT_PRICE_CHANGED). */
  readonly confirmed_unit_price_micros?: number | null;
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
  /** ARCH-30 Tranche 3 (D-11). */
  readonly subscription_status: string | null;
  readonly grace_ends_at: string | null;
  /** Website and payments session (C5): the bank reversed a payment (a lost dispute). */
  readonly reason?: "PAYMENT_REVERSED" | null;
}

export interface CheckoutSessionRequest {
  readonly quota_tier_key: string;
  readonly seats?: number;
  readonly price_id?: string;
  readonly success_url?: string;
  readonly cancel_url?: string;
  // ARCH50-S2:checkout-interval — annual billing, INR, a promo code (all optional).
  readonly interval?: "month" | "year";
  readonly currency?: "USD" | "INR";
  readonly promo_code?: string;
}

export interface PortalSessionRequest {
  readonly return_url?: string;
  /** Campaign session 4 (F-344): a subscriber's pick; the portal opens on the confirmation of it. */
  readonly quota_tier_key?: string;
  readonly seats?: number;
  readonly interval?: "month" | "year";
  readonly currency?: "USD" | "INR";
}

export interface SeatSyncRequest {
  readonly reason?: string;
  readonly force?: boolean;
}

export interface EphemeralSessionResponse {
  readonly url: string;
  /** ARCH39-S1:ts-billing — "checkout" | "portal" | "assigned" (Free, no redirect). */
  readonly kind?: string;
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
  /** ARCH-29 Tranche 2. True when this tier can be bought self-serve. */
  readonly is_priced: boolean;
  readonly entitlements: readonly PlanEntitlement[];
  readonly notes: string | null;
  // ARCH50-S2:plan-prices — every (interval, currency) the plan is sold in.
  readonly prices?: readonly PlanPriceOption[];
}

/** ARCH50-S2:plan-prices. `unit_amount` in minor units; `price_id` null = not sold self-serve (contact sales). */
export interface PlanPriceOption {
  readonly interval: string;
  readonly currency: string;
  readonly unit_amount: number;
  readonly price_id: string | null;
  readonly source: string;
}

export interface PlanListResponse {
  readonly organization_id: string;
  readonly current_tier_key: string | null;
  readonly as_of: string;
  readonly plans: readonly PlanOption[];
  readonly checkout_available?: boolean;
  readonly checkout_unavailable_reason?: string | null;
}

export const microsToUnits = (micros: number): number => micros / 1_000_000;

export const formatMicros = (
  micros: number,
  currency: string = "USD",
): string => formatMoney(microsToUnits(micros), currency);

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

/**
 * ARCH-24 Tranche 4 — the seat figures the JIT policy panel may render.
 *
 * Both money fields are nullable and each is nullable for a different reason:
 *
 *   unit_price_micros === null   the pinned price book has no seat entry.
 *                                A configuration fault, not a free seat.
 *   proration_micros  === null   Stripe could not be reached. The real figure
 *                                is whatever Stripe eventually invoices.
 *
 * Do NOT do arithmetic on these. Rendering `unit_price_micros * seats` as a
 * total would reintroduce, in TypeScript, precisely the local-proration drift
 * ARCH-15 removed from the backend — and the drift gets discovered by a
 * customer reading their invoice, not by us.
 */
export type SeatPriceSource = "PRICE_BOOK" | "UNPRICED";
export type SeatProrationSource = "STRIPE_PREVIEW" | "UNAVAILABLE";

export interface SeatPriceBookResponse {
  readonly organization_id: string;
  readonly seats_current: number;
  readonly seats_after: number;

  readonly unit_price_micros: number | null;
  readonly unit: string | null;
  readonly currency: string;
  readonly price_book_id: string | null;
  readonly price_book_version: number | null;
  readonly price_source: SeatPriceSource;

  readonly proration_micros: number | null;
  readonly proration_source: SeatProrationSource;
  readonly proration_unavailable_reason: string | null;

  readonly period_start: string | null;
  readonly period_end: string | null;
}


/**
 * Campaign session 1 — what the organization's plan admits, readable by every
 * member of a workspace (GET /workspaces/{id}/plan-allowance): the counts a
 * person plans by, the per-file limits, whether the caller may upgrade, and
 * whom to ask otherwise. No money, no invoices.
 */
export type AllowanceState = "OK" | "NEAR" | "REACHED" | "OVER";

export interface AllowanceMeter {
  readonly key: string;
  readonly used: number;
  readonly limit: number;
  readonly hard_stop: boolean;
  readonly overage_policy: string;
  readonly resets_at: string;
  readonly state: AllowanceState;
}

export interface PlanAllowanceResponse {
  readonly plan_key: string | null;
  readonly plan_name: string | null;
  readonly max_file_mb: number;
  readonly max_pages_per_document: number;
  readonly meters: readonly AllowanceMeter[];
  readonly can_upgrade: boolean;
  /** Owners and billing managers, by name: who to ask for more. */
  readonly ask: readonly string[];
  /** Campaign session 2 (N-052): the free trial, read from the same source as the banner. */
  readonly trial?: {
    readonly state: "NONE" | "ACTIVE" | "ENDED";
    readonly ends_at: string | null;
    readonly days_left: number | null;
    /** Campaign session 4 (F-342): ENDED because a paid subscription ended, not a first trial. */
    readonly lapsed?: boolean;
  };
}

const INVOICE_STATUS_LABELS: Readonly<Record<string, string>> = {
  DRAFT: "Draft",
  OPEN: "Open",
  PAID: "Paid",
  VOID: "Void",
  UNCOLLECTIBLE: "Uncollectible",
};

/**
 * What an invoice's status line says (campaign session 4). The list showed the raw status code
 * ("PAID"); a refunded payment now says so, as the gateway's own invoice page does.
 */
export function invoiceStatusLabel(invoice: Pick<InvoiceSummary, "status" | "amount_paid_micros" | "amount_refunded_micros">): string {
  const refunded = invoice.amount_refunded_micros ?? 0;
  if (refunded > 0) {
    return refunded >= invoice.amount_paid_micros ? "Refunded" : "Partially refunded";
  }
  return INVOICE_STATUS_LABELS[invoice.status] ?? invoice.status.charAt(0) + invoice.status.slice(1).toLowerCase();
}

