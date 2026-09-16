/**
 * ARCH-34 — Forensic Audit Radar types.
 *
 * SCORES ARE STRINGS, NOT NUMBERS.
 *
 * The API serialises `numeric(6,5)` as a string because `0.97` does not exist
 * in binary floating point. Typing these as `number` would let a JSON parse
 * turn 0.97 into 0.9699999999999999 and render 96% for a finding the engine
 * scored at exactly the L3 threshold. `percent()` in the API client is the one
 * place the conversion happens, and it happens once, at the edge.
 *
 * `evidence` IS DELIBERATELY LOOSE.
 *
 * Five layers produce five shapes, discriminated by `kind`. A closed union
 * here would have to be edited in lockstep with every backend layer, and the
 * failure mode of getting it wrong is a finding that renders as nothing. The
 * page switches on `kind` and falls back to a labelled block for anything it
 * does not recognise, so an unknown evidence kind degrades to "shown but not
 * formatted" rather than "silently dropped".
 */

export type AnomalyKind = "DUPLICATE_DOCUMENT" | "PRICE_SURGE" | "CONTRACT_DRIFT";

export type AnomalyLayer =
  | "L0"
  | "L1"
  | "L2"
  | "L3"
  | "PRICE_SURGE"
  | "CONTRACT_DRIFT";

export type AnomalySeverity = "LOW" | "MEDIUM" | "HIGH";

export type AnomalyStatus = "OPEN" | "CONFIRMED" | "DISMISSED";

export type SurgeBasis = "ROBUST_Z" | "RELATIVE_ONLY";

export type EvidenceKind =
  | "identifiers"
  | "shingles"
  | "chunk_pair"
  | "price_series"
  | "clause_pair";

export interface AnomalyEvidence {
  readonly kind: EvidenceKind | string;
  readonly label?: string | undefined;
  readonly note?: string | undefined;
  readonly [key: string]: unknown;
}

export interface AnomalyFindingSummary {
  readonly id: string;
  readonly kind: AnomalyKind;
  readonly layer: AnomalyLayer;
  readonly severity: AnomalySeverity;
  readonly status: AnomalyStatus;
  /** Serialised Decimal in [0, 1]. See the file header. */
  readonly score: string;
  readonly headline: string;
  readonly subject_work_item_id: string;
  readonly counterpart_work_item_id?: string | null | undefined;
  readonly subject_label: string;
  readonly counterpart_label: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly resolved_at?: string | null | undefined;
  readonly resolution_note?: string | null | undefined;
}

export interface AnomalyFindingDetail extends AnomalyFindingSummary {
  readonly metrics: Readonly<Record<string, unknown>>;
  readonly evidence: readonly AnomalyEvidence[];
  readonly engine_version: string;
  readonly input_digest: string;
}

export interface AnomalyFeedCounts {
  readonly high: number;
  readonly medium: number;
  readonly low: number;
  readonly open: number;
  readonly confirmed: number;
  readonly dismissed: number;
}

export interface AnomalyFeed {
  readonly counts: AnomalyFeedCounts;
  readonly items: readonly AnomalyFindingSummary[];
}

export interface PriceSeriesPoint {
  readonly work_item_id: string;
  readonly observed_on: string;
  readonly unit_price_micros: number;
}

export interface PriceSeries {
  readonly vendor_key: string;
  readonly sku: string;
  readonly currency: string;
  readonly points: readonly PriceSeriesPoint[];
  readonly median_micros?: string | null | undefined;
  readonly mad_micros?: string | null | undefined;
  readonly iqr_micros?: string | null | undefined;
  /**
   * Null when the series has no historical variation — MAD = 0. The chart
   * says "no historical variation to compare against" rather than drawing a
   * z-score, which is the honest rendering of a fallback decision.
   */
  readonly z?: string | null | undefined;
  readonly basis?: SurgeBasis | null | undefined;
  readonly flagged_work_item_id?: string | null | undefined;
}

export interface AnomalySuppression {
  readonly id: string;
  readonly kind: AnomalyKind;
  readonly layer: AnomalyLayer;
  readonly item_a_id: string;
  readonly item_b_id?: string | null | undefined;
  readonly vendor_key?: string | null | undefined;
  readonly sku?: string | null | undefined;
  readonly reason: string;
  readonly created_at: string;
  readonly expires_at?: string | null | undefined;
}

export interface AnomalyFilters {
  readonly kind?: AnomalyKind | undefined;
  readonly severity?: AnomalySeverity | undefined;
  readonly status?: AnomalyStatus | undefined;
}

/**
 * Plain words for a layer, for the line under the headline.
 *
 * §5.3's requirement that "same invoice number from the same vendor" and
 * "looks similar" are never presented as the same strength of evidence is
 * enforced in the backend by keeping `layer` as its own column. This map is
 * where that distinction reaches the reader.
 */
export const LAYER_LABELS: Readonly<Record<AnomalyLayer, string>> = {
  L0: "Identical file",
  L1: "Same vendor and document number",
  L2: "Line items match",
  L3: "Reads similar",
  PRICE_SURGE: "Price change",
  CONTRACT_DRIFT: "Contract terms",
};

export const KIND_LABELS: Readonly<Record<AnomalyKind, string>> = {
  DUPLICATE_DOCUMENT: "Duplicates",
  PRICE_SURGE: "Price changes",
  CONTRACT_DRIFT: "Contract terms",
};
