/**
 * ARCH40-S2:review-types. The unified review hub's wire contract.
 *
 * One projection over three sources: extraction verifications, clause
 * assertions and radar anomaly findings. Mirrors
 * `backend/app/schemas/review.py`; `verify_arch40.py` gate F2 compares the
 * two field lists so they cannot drift.
 */

// ARCH42-S2:review-kind-merge  ARCH43-S2:review-kind-split  ARCH44-S2:review-kind-table
// ARCH45-S2:review-kind-corroboration
export type ReviewKind = "EXTRACTION" | "ASSERTION" | "ANOMALY" | "MERGE" | "SPLIT" | "TABLE" | "CORROBORATION";
export type ReviewSeverity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type ReviewStatus = "OPEN" | "RESOLVED";

/** Why an item needs a human. Computed by the `review_queue_items` view. */
export type ReviewReason =
  | "DISAGREEMENT"
  | "ESCALATION"
  | "CALIBRATION_HOLD"
  | "AUTONOMY_AUDIT"
  | "PENDING_REVIEW"
  | "CLAUSE_TRIAGE"
  | "ANOMALY"
  | "ENTITY_MERGE"
  | "PACKET_SPLIT"
  | "TABLE_ARITHMETIC"
  | "MATERIAL_DISCREPANCY";

export const REVIEW_KINDS: readonly ReviewKind[] = ["EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE", "CORROBORATION"];
export const REVIEW_SEVERITIES: readonly ReviewSeverity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

/** The "Autonomy audits" tab: ARCH-35 holds and accuracy-audit samples. */
export const AUTONOMY_REASONS: readonly ReviewReason[] = ["AUTONOMY_AUDIT", "CALIBRATION_HOLD"];

export const KIND_LABELS: Readonly<Record<ReviewKind, string>> = {
  EXTRACTION: "Extraction",
  ASSERTION: "Clause",
  ANOMALY: "Anomaly",
  MERGE: "Entity merge",
  SPLIT: "Packet split",
  TABLE: "Table",
  CORROBORATION: "Comparison",
};

export const REASON_LABELS: Readonly<Record<ReviewReason, string>> = {
  DISAGREEMENT: "Agents disagreed",
  ESCALATION: "Escalated by a rule",
  CALIBRATION_HOLD: "Held by calibration",
  AUTONOMY_AUDIT: "Accuracy audit",
  PENDING_REVIEW: "Awaiting review",
  CLAUSE_TRIAGE: "Below clause threshold",
  ANOMALY: "Radar finding",
  ENTITY_MERGE: "Possible duplicate record",
  PACKET_SPLIT: "Scanned packet to divide",
  TABLE_ARITHMETIC: "Figures do not reconcile",
  MATERIAL_DISCREPANCY: "Documents disagree",
};

export interface ReviewItem {
  readonly kind: ReviewKind;
  readonly item_id: string;
  readonly work_item_id: string | null;
  readonly document_name: string | null;
  readonly headline: string;
  readonly severity: ReviewSeverity;
  readonly confidence: number | null;
  readonly created_at: string;
  readonly age_seconds: number;
  readonly status: ReviewStatus;
  readonly assignee_user_id: string | null;
  readonly assignee_email: string | null;
  readonly under_retention_hold: boolean;
  readonly tags: readonly string[];
  readonly review_reason: ReviewReason | "";
}

export interface ReviewQueue {
  readonly items: readonly ReviewItem[];
  readonly total: number;
  readonly page: number;
  readonly page_size: number;
  readonly counts_by_kind: Readonly<Record<string, number>>;
  /** What the plan lets this organization see. Tabs are drawn from it. */
  readonly allowed_kinds: readonly ReviewKind[];
}

export interface ReviewQueueFilters {
  readonly kind?: readonly ReviewKind[];
  readonly severity?: readonly ReviewSeverity[];
  readonly reason?: readonly ReviewReason[];
  readonly status?: ReviewStatus;
  readonly tag?: string;
  readonly assignee_user_id?: string;
  readonly unassigned_only?: boolean;
  readonly min_age_seconds?: number;
  readonly page?: number;
  readonly page_size?: number;
}

export type AssertionVerdict = "PASS" | "FAIL" | "UNDETERMINED";
export type AnomalyVerdict = "CONFIRM" | "DISMISS";
export type MergeVerdict = "MERGE" | "SEPARATE";
export type SplitVerdict = "APPROVE" | "REJECT";
export type TableReviewVerdict = "ACCEPT" | "REJECT";
export type CorroborationVerdict = "CONFIRM" | "DISMISS";

export interface ReviewResolveRequest {
  readonly values?: Readonly<Record<string, unknown>>;
  readonly reviewer_verdict?: AssertionVerdict;
  readonly corrected_quote?: string;
  readonly anomaly_verdict?: AnomalyVerdict;
  readonly note?: string;
  readonly ttl_days?: number;
  readonly merge_verdict?: MergeVerdict;
  readonly split_verdict?: SplitVerdict;
  readonly split_boundaries?: readonly number[];
  readonly table_verdict?: TableReviewVerdict;
  readonly corroboration_verdict?: CorroborationVerdict;
}

export interface ReviewResolveResponse {
  readonly kind: ReviewKind;
  readonly item_id: string;
  readonly work_item_id: string | null;
  readonly resolution: string;
}

export type ReviewBulkAction = "resolve" | "assign" | "unassign";

/** ARCH-38's bulk request shape, with `kind` as a sibling field. */
export interface ReviewBulkRequest {
  readonly action: ReviewBulkAction;
  readonly kind: ReviewKind;
  readonly ids: readonly string[];
  readonly idempotency_key: string;
  readonly assignee_user_id?: string;
  readonly payload?: ReviewResolveRequest;
}

/** ARCH-38's `ItemResult`, field for field, plus the review item id. */
export interface ReviewBulkItemResult {
  readonly work_item_id: string;
  readonly outcome: "ok" | "refused" | "skipped";
  readonly code: string | null;
  readonly detail: string | null;
  readonly review_item_id: string | null;
}

export interface ReviewBulkResponse {
  readonly action: ReviewBulkAction;
  readonly kind: ReviewKind;
  readonly results: readonly ReviewBulkItemResult[];
  readonly ok: number;
  readonly refused: number;
  readonly skipped: number;
}

export interface ReviewAssignee {
  readonly user_id: string;
  readonly email: string;
  readonly open_items: number;
}

/** Oldest first within a severity; the server sorts, this only formats. */
export const formatAge = (seconds: number): string => {
  if (seconds < 3600) {
    return `${Math.max(1, Math.round(seconds / 60))}m`;
  }
  if (seconds < 86_400) {
    return `${Math.round(seconds / 3600)}h`;
  }
  return `${Math.round(seconds / 86_400)}d`;
};
