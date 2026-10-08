/** Phase 1 — batch operations (app/schemas/batches.py). */

export type Lane = "STRAIGHT_THROUGH" | "REVIEW" | "EXCEPTION";
export type SchemaState = "NO_SCHEMA" | "HEALTHY" | "HEALABLE" | "NEEDS_ATTENTION";
export type PackageStatus = "QUEUED" | "BUILDING" | "READY" | "FAILED" | "EXPIRED";
export type Verdict = "VERIFIED" | "TAMPERED" | "UNRECOGNISED" | "INVALID";

export interface BatchProgress {
  readonly documents: number;
  readonly queued: number;
  readonly processing: number;
  readonly completed: number;
  readonly failed: number;
  readonly percent: number;
  readonly straight_through: number;
  readonly review: number;
  readonly exception: number;
  readonly mean_confidence: number | null;
}

export interface BatchSummary {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly source: "SELECTION" | "INGESTION";
  readonly status: "ACTIVE" | "ARCHIVED";
  readonly ingestion_batch_id: string | null;
  readonly created_by_user_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly dispatched_at: string | null;
  readonly progress: BatchProgress;
}

export interface BatchList {
  readonly items: readonly BatchSummary[];
  readonly total: number;
}

export interface HealingChange {
  readonly kind: "RENAME" | "RETYPE";
  readonly field: string;
  readonly from_field: string | null;
  readonly before: unknown;
  readonly after: unknown;
  readonly note: string;
}

export interface HealingIssue {
  readonly kind: "MISSING_REQUIRED" | "UNPARSEABLE" | "AMBIGUOUS_DATE" | "CONFLICT";
  readonly field: string;
  readonly message: string;
  readonly value: unknown;
}

export interface BatchDocument {
  readonly work_item_id: string;
  readonly original_filename: string;
  readonly status: "QUEUED" | "PROCESSING" | "COMPLETED" | "FAILED";
  readonly document_type: string | null;
  readonly added_at: string;
  readonly confidence: number | null;
  readonly verification_status: string | null;
  readonly lane: Lane | null;
  readonly reasons: readonly string[];
  readonly dispatched_lane: Lane | null;
  readonly dispatched_at: string | null;
  readonly schema_key: string | null;
  readonly schema_label: string | null;
  readonly schema_state: SchemaState;
  readonly completeness: number | null;
  readonly changes: readonly HealingChange[];
  readonly issues: readonly HealingIssue[];
  readonly failure_reason: string | null;
}

export interface DispatchPolicy {
  readonly straight_through_min_confidence: number;
  readonly review_min_confidence: number;
  readonly require_required_fields: boolean;
  readonly tag_documents: boolean;
  readonly is_default: boolean;
  readonly updated_at: string | null;
}

export interface BatchDetail {
  readonly batch: BatchSummary;
  readonly policy: DispatchPolicy;
  readonly documents: readonly BatchDocument[];
}

export interface HistogramBucket {
  readonly label: string;
  readonly low: number;
  readonly high: number;
  readonly count: number;
}

export interface FieldStat {
  readonly field: string;
  readonly documents: number;
  readonly mean_confidence: number | null;
  readonly min_confidence: number | null;
  readonly disagreement_rate: number | null;
  readonly missing_required: number;
}

export interface TypeStat {
  readonly document_type: string;
  readonly documents: number;
  readonly mean_confidence: number | null;
  readonly straight_through: number;
  readonly review: number;
  readonly exception: number;
}

export interface BatchAnalytics {
  readonly documents: number;
  readonly status: { readonly queued: number; readonly processing: number; readonly completed: number; readonly failed: number };
  readonly confidence: {
    readonly scored: number;
    readonly unscored: number;
    readonly mean: number | null;
    readonly median: number | null;
    readonly histogram: readonly HistogramBucket[];
    readonly field_histogram: readonly HistogramBucket[];
  };
  readonly fields: readonly FieldStat[];
  readonly document_types: readonly TypeStat[];
  readonly lanes: { readonly straight_through: number; readonly review: number; readonly exception: number; readonly pending: number };
  readonly straight_through_rate: number | null;
  readonly schema: {
    readonly healthy: number;
    readonly healable: number;
    readonly needs_attention: number;
    readonly no_schema: number;
    readonly missing_required: number;
  };
  readonly throughput: {
    readonly median_seconds: number | null;
    readonly p95_seconds: number | null;
    readonly documents_per_hour: number | null;
  };
}

export interface DispatchResult {
  readonly dispatched_at: string;
  readonly straight_through: number;
  readonly review: number;
  readonly exception: number;
  readonly pending: number;
  readonly tagged: boolean;
}

export interface HealOutcome {
  readonly work_item_id: string;
  readonly outcome: "healed" | "refused";
  readonly event_id: string | null;
  readonly changes: number | null;
  readonly code: string | null;
  readonly message: string | null;
}

export interface HealResult {
  readonly healed: number;
  readonly refused: number;
  readonly skipped: number;
  readonly results: readonly HealOutcome[];
}

export interface RetryResult {
  readonly requeued: number;
  readonly refused: number;
}

export interface ExportPackage {
  readonly id: string;
  readonly name: string;
  readonly batch_id: string | null;
  readonly status: PackageStatus;
  readonly include_originals: boolean;
  readonly document_count: number;
  readonly file_count: number;
  readonly size_bytes: number;
  readonly package_sha256: string | null;
  readonly manifest_sha256: string | null;
  readonly error_code: string | null;
  readonly error_detail: string | null;
  readonly download_count: number;
  readonly created_by_user_id: string | null;
  readonly created_at: string;
  readonly completed_at: string | null;
  readonly expires_at: string | null;
  readonly last_downloaded_at: string | null;
}

export interface PackageList {
  readonly items: readonly ExportPackage[];
  readonly total: number;
}

export interface PackageManifest {
  readonly package: ExportPackage;
  readonly files: readonly { readonly path: string; readonly sha256: string; readonly bytes: number }[];
}

export interface VerifyResult {
  readonly verdict: Verdict;
  readonly message: string;
  readonly package_id: string | null;
  readonly package_name: string | null;
  readonly issued_at: string | null;
  readonly files_checked: number;
  readonly files_ok: number;
  readonly problems: readonly { readonly path: string; readonly expected: string | null; readonly actual: string | null; readonly status: string }[];
  readonly manifest_sha256: string | null;
  readonly recorded_manifest_sha256: string | null;
  readonly archive_sha256: string;
  readonly archive_matches_record: boolean | null;
  readonly manifest_matches_record: boolean | null;
}

export const LANE_LABELS: Readonly<Record<Lane, string>> = {
  STRAIGHT_THROUGH: "Straight through",
  REVIEW: "Human review",
  EXCEPTION: "Exception",
};

export const SCHEMA_STATE_LABELS: Readonly<Record<SchemaState, string>> = {
  NO_SCHEMA: "No schema",
  HEALTHY: "Matches schema",
  HEALABLE: "Can be healed",
  NEEDS_ATTENTION: "Needs attention",
};
