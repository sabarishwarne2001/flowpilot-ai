/**
 * ARCH-18 / ARCH-24 — COGS, unit economics and supplier reconciliation DTOs.
 *
 * Every `| null` in this file is load-bearing. The backend returns null for a
 * margin it cannot compute, a share it cannot take, and a ratio that is
 * undefined — and the UI must render those as "unknown" rather than as zero.
 * Widening any of these to a plain `number` would let a `?? 0` slip in at the
 * call site and turn "we don't know what this cost" into "this was free",
 * which reads on the dashboard as a 100% gross margin.
 *
 * Per-unit prices arrive as strings because they carry nine decimal places
 * and would lose precision through a JSON float. They are display-only; every
 * total is an integer micros field.
 */

/* -------------------------------------------------------------------------
 * Margins
 * ---------------------------------------------------------------------- */

export interface MarginFigures {
  readonly revenue_micros: number;
  /** Revenue on rows that also carry a cost basis — the margin's denominator. */
  readonly attributed_revenue_micros: number;
  readonly cost_basis_micros: number;
  readonly unknown_cost_revenue_micros: number;

  /** null when no row in the window has a known cost. */
  readonly gross_margin_micros: number | null;
  readonly gross_margin_ratio: number | null;

  /** Share of revenue excluded from the margin, by value. Read this first. */
  readonly unknown_cost_share: number | null;
  /** Share of attributed revenue whose cost is ESTIMATED rather than measured. */
  readonly soft_cost_share: number | null;

  readonly event_count: number;
  readonly known_cost_event_count: number;
  readonly unknown_cost_event_count: number;

  /** False when too little revenue has a known cost for the margin to be quoted. */
  readonly is_trustworthy: boolean;
}

export interface PlatformMarginSummary {
  readonly period_start: string;
  readonly period_end: string;
  readonly currency: string;
  readonly organization_count: number;
  readonly figures: MarginFigures;
}

export interface TenantEconomicsEntry {
  readonly organization_id: string;
  readonly organization_name: string | null;
  readonly organization_slug: string | null;
  readonly figures: MarginFigures;
}

export type MarginOrder =
  | "MARGIN_ASC"
  | "MARGIN_DESC"
  | "REVENUE_DESC"
  | "UNKNOWN_DESC";

export interface TenantEconomicsResponse {
  readonly period_start: string;
  readonly period_end: string;
  readonly currency: string;
  readonly order: MarginOrder;
  readonly entries: readonly TenantEconomicsEntry[];
}

export interface ProviderCostEntry {
  readonly provider: string | null;
  readonly cost_basis_micros: number;
  readonly revenue_micros: number;
  readonly event_count: number;
  readonly unknown_cost_event_count: number;
}

export interface ProviderCostResponse {
  readonly period_start: string;
  readonly period_end: string;
  readonly entries: readonly ProviderCostEntry[];
}

/* -------------------------------------------------------------------------
 * Rate card
 * ---------------------------------------------------------------------- */

export type CostBasisSource =
  | "SUPPLIER_RATE_CARD"
  | "MEASURED"
  | "ESTIMATED"
  | "ZERO_BYOK";

export interface RateCardEntry {
  readonly event_type: string;
  readonly provider: string;
  readonly model: string | null;
  readonly tier_key: string | null;
  readonly unit: string;
  readonly unit_price_micros: string;
  readonly cost_basis_micros: string | null;
  readonly cost_basis_source: CostBasisSource | null;
  readonly unit_margin_micros: string | null;
  readonly notes: string | null;
}

export interface RateCardResponse {
  readonly price_book_id: string | null;
  readonly price_book_version: number | null;
  readonly currency: string | null;
  readonly effective_from: string | null;
  readonly entry_count: number;
  readonly with_cost_basis: number;
  readonly hard_cost_basis: number;
  readonly coverage_ratio: number | null;
  readonly entries: readonly RateCardEntry[];
}

/* -------------------------------------------------------------------------
 * Supplier invoices
 * ---------------------------------------------------------------------- */

export type ReconciliationStatus = "MATCHED" | "INVESTIGATE" | "ACCEPTED";

/**
 * ARCH-24. Which denominator produced a variance figure.
 *
 * `ARCH14_SELL_SIDE` is denominated in customer price and is inflated by our
 * own gross margin. It is NOT cost variance and must never be rendered as
 * COGS, however tempting the adjacency.
 */
export type CostBasisMethod =
  | "ARCH18_SUPPLIER_COST"
  | "ARCH18_PRE_CONSOLIDATION"
  | "ARCH14_SELL_SIDE";

/** ARCH-24 N-3. Where a supplier invoice row came from. */
export type SupplierInvoiceOrigin = "STATEMENT_PULL" | "OPERATOR_UPLOAD";

export interface SupplierReconciliation {
  readonly id: string;
  readonly supplier_invoice_id: string;
  readonly modelled_total_micros: number;
  readonly variance_micros: number;
  /** null when the modelled total is zero — undefined, not a perfect match. */
  readonly variance_ratio: number | null;
  readonly status: ReconciliationStatus;
  readonly modelled_event_count: number;
  readonly unknown_cost_event_count: number;
  /** ARCH-24: Denominator method indicator. */
  readonly cost_basis_method: CostBasisMethod;
  /** ARCH-24: True when variance is denominated in genuine supplier cost. */
  readonly is_authoritative_cost: boolean;
  readonly note: string | null;
  readonly reconciled_at: string;
  readonly reconciled_by_user_id: string | null;
}

export interface SupplierInvoice {
  readonly id: string;
  readonly provider: string;
  readonly invoice_reference: string | null;
  readonly period_start: string;
  /** Last day covered, INCLUSIVE. */
  readonly period_end: string;
  readonly invoiced_total_micros: number;
  readonly currency: string;
  readonly raw_document_file_id: string | null;
  readonly ingested_at: string;
  readonly ingested_by_user_id: string | null;
  readonly notes: string | null;
  readonly latest_reconciliation: SupplierReconciliation | null;
  readonly origin: SupplierInvoiceOrigin;
  readonly superseded_invoice_id: string | null;
}

export interface ConsolidatedReconciliationResponse {
  readonly entries: readonly SupplierReconciliation[];
  readonly authoritative_method: CostBasisMethod;
  /**
   * ARCH-14 runs in the same window. A count only, deliberately: their drift
   * figures are customer-price denominated and showing them beside these rows
   * would invite exactly the misreading ARCH-24 removed.
   */
  readonly sell_side_run_count: number;
}

export interface RollupCostBasisEntry {
  readonly organization_id: string;
  readonly granularity: string;
  readonly bucket_start: string;
  readonly event_type: string;
  readonly provider: string | null;
  readonly event_count: number;
  readonly cost_micros: number;
  /**
   * NULL means unknown, not zero. Buckets sealed before ARCH-24 are
   * permanently null by design. Render "unknown" — never a dash that reads
   * as free, and never a margin computed against it.
   */
  readonly cost_basis_micros: number | null;
  readonly unknown_cost_basis_event_count: number;
  readonly cost_basis_source_mix: Readonly<Record<string, number>> | null;
  /** Computed by the backend. The client never recomputes the threshold. */
  readonly is_trustworthy: boolean;
}

export interface SupplierInvoiceListResponse {
  readonly entries: readonly SupplierInvoice[];
}

export interface SupplierInvoiceCreateRequest {
  readonly provider: string;
  readonly period_start: string;
  readonly period_end: string;
  readonly invoiced_total_micros: number;
  readonly currency?: string;
  readonly invoice_reference?: string | null;
  readonly notes?: string | null;
}

export interface ReconcileRequest {
  readonly note?: string | null;
  readonly threshold_ratio?: number | null;
  readonly force?: boolean;
}

export interface AcceptVarianceRequest {
  readonly note: string;
}

/* -------------------------------------------------------------------------
 * Formatting
 * ---------------------------------------------------------------------- */

export const UNKNOWN_LABEL = "unknown";

/** Micros to a currency string. 1_000_000 micros = 1 unit. */
export const formatMicros = (
  micros: number | null | undefined,
  currency = "USD",
): string => {
  if (micros === null || micros === undefined) {
    return UNKNOWN_LABEL;
  }
  const units = micros / 1_000_000;
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: Math.abs(units) < 1 ? 4 : 2,
    }).format(units);
  } catch {
    return `${units.toFixed(2)} ${currency}`;
  }
};

export const formatRatio = (ratio: number | null | undefined): string => {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) {
    return UNKNOWN_LABEL;
  }
  return `${(ratio * 100).toFixed(1)}%`;
};

export const formatSignedMicros = (
  micros: number | null | undefined,
  currency = "USD",
): string => {
  if (micros === null || micros === undefined) {
    return UNKNOWN_LABEL;
  }
  const sign = micros > 0 ? "+" : "";
  return `${sign}${formatMicros(micros, currency)}`;
};

/**
 * How much of a margin figure to believe.
 *
 * Mirrors margin_service.MIN_TRUSTWORTHY_KNOWN_SHARE, but the UI never
 * recomputes the verdict — the backend sends `is_trustworthy` and this only
 * chooses the wording.
 */
export const confidenceLabel = (figures: MarginFigures): string => {
  if (figures.event_count === 0) {
    return "No usage in this period";
  }
  if (figures.gross_margin_micros === null) {
    return "No cost basis recorded for any usage in this period";
  }
  if (!figures.is_trustworthy) {
    return `Margin excludes ${formatRatio(figures.unknown_cost_share)} of revenue`;
  }
  if ((figures.soft_cost_share ?? 0) > 0.2) {
    return `${formatRatio(figures.soft_cost_share)} of cost is estimated`;
  }
  return "Cost basis known for most revenue";
};

export const statusTone = (
  status: ReconciliationStatus,
): "ok" | "warn" | "muted" => {
  switch (status) {
    case "MATCHED":
      return "ok";
    case "INVESTIGATE":
      return "warn";
    case "ACCEPTED":
    default:
      return "muted";
  }
};
