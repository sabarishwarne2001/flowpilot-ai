/**
 * ARCH43-S2:types-cases — case intelligence API shapes. Mirrors
 * backend/app/schemas/cases.py field for field (verify_arch43 compares them).
 */

export type CaseStatus = "INCOMPLETE" | "INCONSISTENT" | "COMPLETE" | "CLOSED";
export type RuleOutcome = "PASS" | "FAIL" | "MISSING" | "ERROR";
export const CASE_STATUSES: readonly CaseStatus[] = ["INCOMPLETE", "INCONSISTENT", "COMPLETE", "CLOSED"];
export const CASE_STATUS_LABELS: Readonly<Record<CaseStatus, string>> = {
  INCOMPLETE: "Waiting for documents",
  INCONSISTENT: "Inconsistent",
  COMPLETE: "Complete",
  CLOSED: "Closed",
};

export interface TemplateRow {
  readonly id: string;
  readonly key: string;
  readonly version: number;
  readonly name: string;
  readonly description: string;
  readonly status: "DRAFT" | "PUBLISHED" | "RETIRED";
  readonly assembly_key: "ENTITY" | "BATCH" | "MANUAL";
  readonly entity_kind: string | null;
  readonly entity_role: string | null;
  readonly required_documents: readonly Readonly<Record<string, unknown>>[];
  readonly rules: readonly Readonly<Record<string, unknown>>[];
  readonly request_ttl_hours: number;
  readonly published_at: string | null;
}

export interface TemplateWrite {
  readonly key: string;
  readonly name: string;
  readonly description?: string;
  readonly assembly_key: string;
  readonly entity_kind?: string | null;
  readonly entity_role?: string | null;
  readonly required_documents: readonly Readonly<Record<string, unknown>>[];
  readonly rules: readonly Readonly<Record<string, unknown>>[];
  readonly request_ttl_hours?: number;
}

export interface CaseRow {
  readonly id: string;
  readonly title: string;
  readonly status: CaseStatus;
  readonly completeness: number;
  readonly template_id: string;
  readonly template_key: string;
  readonly anchor_kind: string;
  readonly anchor_entity_id: string | null;
  readonly anchor_batch_id: string | null;
  readonly documents: number;
  readonly failed_rules: number;
  readonly created_at: string;
  readonly evaluated_at: string | null;
}

export interface CaseList {
  readonly items: readonly CaseRow[];
  readonly counts_by_status: Readonly<Record<string, number>>;
}

export interface ChecklistSlot {
  readonly doc_type: string;
  readonly label: string;
  readonly min_count: number;
  readonly present: number;
  readonly satisfied: boolean;
}

export interface CaseDocumentRow {
  readonly work_item_id: string;
  readonly original_filename: string;
  readonly document_type: string;
  readonly source: string;
  readonly added_at: string;
}

export interface RuleResultRow {
  readonly rule_id: string;
  readonly label: string;
  readonly op: string;
  readonly outcome: RuleOutcome;
  readonly left_value: string | null;
  readonly right_value: string | null;
  readonly detail: Readonly<Record<string, unknown>>;
}

export interface RequestRow {
  readonly id: string;
  readonly document_type: string;
  readonly recipient_label: string;
  readonly status: "OPEN" | "FULFILLED" | "EXPIRED" | "REVOKED";
  readonly expires_at: string;
  readonly used_at: string | null;
  readonly fulfilled_work_item_id: string | null;
}

export interface CaseDetail {
  readonly case: CaseRow;
  readonly template: TemplateRow;
  readonly checklist: readonly ChecklistSlot[];
  readonly documents: readonly CaseDocumentRow[];
  readonly rules: readonly RuleResultRow[];
  readonly requests: readonly RequestRow[];
}

export interface RequestCreated {
  readonly request: RequestRow;
  readonly token: string;
  readonly upload_path: string;
}

export interface PublicRequestInfo {
  readonly document_type: string;
  readonly case_title: string;
  readonly expires_at: string;
}

export interface PublicUploadResult {
  readonly received: boolean;
  readonly document_type: string;
}
