/**
 * ARCH45-S2:types — the document corroborator's wire contract. Mirrors
 * backend/app/schemas/corroboration.py; verify_arch45 T9 compares the field lists.
 */

export type RunStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "STALE" | "FAILED";
export type Layer = "FIELD" | "ENTITY" | "CLAUSE" | "TABLE" | "RULE";
export type DiscrepancyKind =
  | "FIELD_MISMATCH" | "FIELD_MISSING" | "ENTITY_MISMATCH" | "ENTITY_MISSING" | "CLAUSE_MODIFIED" | "CLAUSE_MISSING"
  | "LINE_MISMATCH" | "LINE_MISSING" | "RULE_CONFLICT" | "RULE_VALUE" | "RULE_FAILED";
export type Severity = "HIGH" | "MEDIUM" | "LOW";
export type Decision = "OPEN" | "CONFIRMED" | "DISMISSED";
export type RunVerdict = "CONFIRM" | "DISMISS";
export type ExportFormat = "pdf" | "csv" | "json";

export const LAYERS: readonly Layer[] = ["FIELD", "ENTITY", "CLAUSE", "TABLE", "RULE"];
export const MIN_DOCUMENTS = 2;
export const MAX_DOCUMENTS = 5;

export const LAYER_LABELS: Readonly<Record<Layer, string>> = {
  FIELD: "Fields", ENTITY: "Parties", CLAUSE: "Clauses", TABLE: "Line items", RULE: "Rules",
};

export const KIND_LABELS: Readonly<Record<DiscrepancyKind, string>> = {
  FIELD_MISMATCH: "Value differs", FIELD_MISSING: "Value absent", ENTITY_MISMATCH: "Different party",
  ENTITY_MISSING: "Party absent", CLAUSE_MODIFIED: "Clause changed", CLAUSE_MISSING: "Clause absent",
  LINE_MISMATCH: "Line differs", LINE_MISSING: "Line absent", RULE_CONFLICT: "Rule pass/fail differs",
  RULE_VALUE: "Rule value differs", RULE_FAILED: "Rule fails everywhere",
};

export const STATUS_LABELS: Readonly<Record<RunStatus, string>> = {
  QUEUED: "Queued", RUNNING: "Comparing", COMPLETED: "Current", STALE: "Out of date", FAILED: "Failed",
};

export const STATUS_TONE: Readonly<Record<RunStatus, string>> = {
  QUEUED: "bg-muted text-muted-foreground",
  RUNNING: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  COMPLETED: "bg-green-500/15 text-green-700 dark:text-green-300",
  STALE: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  FAILED: "bg-red-500/15 text-red-700 dark:text-red-300",
};

export const SEVERITY_TONE: Readonly<Record<Severity, string>> = {
  HIGH: "bg-red-600 text-white",
  MEDIUM: "bg-amber-500 text-white",
  LOW: "bg-muted text-muted-foreground",
};

export const DECISION_LABELS: Readonly<Record<Decision, string>> = {
  OPEN: "Open", CONFIRMED: "Confirmed", DISMISSED: "Dismissed",
};

export interface RunDocumentBrief {
  readonly work_item_id: string;
  readonly position: number;
  readonly label: string;
}

export interface RunSummary {
  readonly id: string;
  readonly status: RunStatus;
  readonly document_count: number;
  readonly discrepancy_count: number;
  readonly material_count: number;
  readonly open_material_count: number;
  readonly max_materiality: number;
  readonly encoder: string;
  readonly engine_version: string;
  readonly fingerprint: string;
  readonly set_hash: string;
  readonly error?: string | null;
  readonly created_by_user_id?: string | null;
  readonly created_at: string;
  readonly completed_at?: string | null;
  readonly stale_at?: string | null;
  readonly reviewed_at?: string | null;
  readonly documents: readonly RunDocumentBrief[];
}

export interface RunList {
  readonly items: readonly RunSummary[];
  readonly total: number;
  readonly counts_by_status: Readonly<Record<string, number>>;
}

export interface PageGeometry {
  readonly page: number;
  readonly width: number;
  readonly height: number;
}

export interface RunDocument {
  readonly work_item_id: string;
  readonly position: number;
  readonly label: string;
  readonly page_count?: number | null;
  readonly content_hash: string;
  readonly file_type: string;
  readonly renderable: boolean;
  readonly changed_since: boolean;
  readonly pages: readonly PageGeometry[];
}

/** One document's value for a discrepancy (discrepancies.doc_values[work_item_id]). */
export interface DocValue {
  readonly present: boolean;
  readonly display: string;
  readonly normalized: string | null;
  readonly page: number | null;
  readonly participates?: boolean;
  readonly [extra: string]: unknown;
}

export interface EvidenceSpan {
  readonly work_item_id: string;
  readonly page: number;
  readonly text: string;
  readonly bbox?: { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number };
}

export interface DiscrepancyRow {
  readonly id: string;
  readonly ordinal: number;
  readonly layer: Layer;
  readonly kind: DiscrepancyKind;
  readonly group_key: string;
  readonly label: string;
  readonly summary: string;
  readonly materiality: number;
  readonly severity: Severity;
  readonly is_material: boolean;
  readonly values: Readonly<Record<string, DocValue>>;
  readonly evidence: readonly EvidenceSpan[];
  readonly detail: Readonly<Record<string, unknown>>;
  readonly status: Decision;
  readonly decided_at?: string | null;
  readonly decided_by_user_id?: string | null;
  readonly note?: string | null;
}

export interface PairRow {
  readonly left_work_item_id: string;
  readonly right_work_item_id: string;
  readonly clauses_left: number;
  readonly clauses_right: number;
  readonly clauses_matched: number;
  readonly clauses_identical: number;
  readonly clause_similarity: number;
  readonly fields_compared: number;
  readonly fields_agreeing: number;
  readonly lines_left: number;
  readonly lines_right: number;
  readonly lines_matched: number;
  readonly lines_agreeing: number;
  readonly entities_compared: number;
  readonly entities_agreeing: number;
  readonly discrepancy_count: number;
  readonly material_count: number;
  readonly agreement: number;
  readonly alignment: readonly unknown[];
}

export interface RuleRow {
  readonly key: string;
  readonly sentence: string;
  readonly source: string;
  readonly family: string;
  readonly understood_as: string;
  readonly digest: string;
  readonly definition_id?: string | null;
}

/** A clause aligned across documents: where it starts in each (the synchronized viewer's anchors). */
export interface Anchor {
  readonly key: string;
  readonly members: Readonly<Record<string, { readonly page: number; readonly y: number | null }>>;
}

export interface RunDetail {
  readonly run: RunSummary;
  readonly stale: boolean;
  readonly options: Readonly<Record<string, unknown>>;
  readonly layers: Readonly<Record<string, unknown>>;
  readonly stats: Readonly<Record<string, unknown>>;
  readonly anchors: readonly Anchor[];
  readonly documents: readonly RunDocument[];
  readonly discrepancies: readonly DiscrepancyRow[];
  readonly pairs: readonly PairRow[];
  readonly rules: readonly RuleRow[];
}

export interface RequestResult {
  readonly run: RunSummary;
  readonly cached: boolean;
}

export interface CorroborationRequest {
  readonly work_item_ids: readonly string[];
  readonly rules?: readonly string[];
  readonly use_workspace_rules?: boolean;
  readonly materiality_threshold?: number;
  readonly money_tolerance?: number;
  readonly relative_tolerance?: number;
  readonly layers?: readonly Layer[];
  readonly force?: boolean;
}

export interface DecisionRequest {
  readonly status: Decision;
  readonly note?: string | null;
}

export interface ReviewRunRequest {
  readonly verdict: RunVerdict;
}

export interface ReviewRunResult {
  readonly decided: number;
  readonly run: RunSummary;
}

export interface WorkspaceRules {
  readonly rules: readonly RuleRow[];
  readonly skipped: readonly Readonly<Record<string, unknown>>[];
}

export interface DocumentComparisons {
  readonly work_item_id: string;
  readonly original_filename: string;
  readonly runs: readonly RunSummary[];
}

/** Whether the run is still being computed (the page polls while it is). */
export const isPending = (status: RunStatus): boolean => status === "QUEUED" || status === "RUNNING";
