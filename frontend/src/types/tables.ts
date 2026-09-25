/**
 * ARCH44-S2:types — the table extractor's wire contract. Mirrors
 * backend/app/schemas/tables.py; verify_arch44 T9 compares the field lists.
 */

export type TableStatus = "EXTRACTED" | "VALIDATED" | "FLAGGED" | "REVIEWED" | "REJECTED";
export type TableMethod = "STREAM" | "LATTICE" | "HYBRID";
export type RowKind = "HEADER" | "BODY" | "SECTION" | "SUBTOTAL" | "TOTAL" | "CARRY";
export type ValueType = "TEXT" | "NUMBER" | "MONEY" | "DATE" | "PERCENT" | "EMPTY";
export type CellFlag = "ARITH_FAIL" | "TYPE_MISMATCH" | "LOW_OCR" | "SPILL" | "CORRECTED";
export type CheckKind = "RUNNING_BALANCE" | "ROW_PRODUCT" | "ROW_TOTAL" | "COLUMN_SUM" | "HIERARCHY_SUM" | "CARRY_FORWARD";
export type TableVerdict = "ACCEPT" | "REJECT";
export type ExportFormat = "csv" | "xlsx" | "json";

export const COLUMN_ROLES = [
  "DATE", "VALUE_DATE", "DESCRIPTION", "REFERENCE", "DEBIT", "CREDIT", "AMOUNT", "BALANCE", "QUANTITY",
  "UNIT_PRICE", "TAX", "DISCOUNT", "TOTAL", "CODE", "UNIT", "RANGE", "PERCENT", "OTHER",
] as const;
export type ColumnRole = (typeof COLUMN_ROLES)[number];

export const ROLE_LABELS: Readonly<Record<ColumnRole, string>> = {
  DATE: "Date", VALUE_DATE: "Value date", DESCRIPTION: "Description", REFERENCE: "Reference", DEBIT: "Debit",
  CREDIT: "Credit", AMOUNT: "Amount", BALANCE: "Balance", QUANTITY: "Quantity", UNIT_PRICE: "Unit price", TAX: "Tax",
  DISCOUNT: "Discount", TOTAL: "Total", CODE: "Code", UNIT: "Unit", RANGE: "Range", PERCENT: "Percent", OTHER: "Other",
};

export const STATUS_LABELS: Readonly<Record<TableStatus, string>> = {
  EXTRACTED: "Extracted", VALIDATED: "Reconciles", FLAGGED: "Needs review", REVIEWED: "Reviewed", REJECTED: "Rejected",
};

export const STATUS_TONE: Readonly<Record<TableStatus, string>> = {
  EXTRACTED: "bg-muted text-muted-foreground",
  VALIDATED: "bg-green-500/15 text-green-700 dark:text-green-300",
  FLAGGED: "bg-red-500/15 text-red-700 dark:text-red-300",
  REVIEWED: "bg-blue-500/15 text-blue-700 dark:text-blue-300",
  REJECTED: "bg-muted text-muted-foreground line-through",
};

export const CHECK_LABELS: Readonly<Record<CheckKind, string>> = {
  RUNNING_BALANCE: "Running balance", ROW_PRODUCT: "Quantity × rate", ROW_TOTAL: "Row total", COLUMN_SUM: "Column total",
  HIERARCHY_SUM: "Subtotal", CARRY_FORWARD: "Carried forward",
};

export interface TableColumn {
  readonly index: number;
  readonly path: readonly string[];
  readonly key: string;
  readonly header_key: string;
  readonly value_type: ValueType;
  readonly date_order: string;
  readonly role: ColumnRole;
  readonly role_source: string;
}

export interface TableRowInfo {
  readonly index: number;
  readonly kind: RowKind;
  readonly level: number;
  readonly page: number;
  readonly parent: number | null;
}

export interface TableCell {
  readonly row: number;
  readonly col: number;
  readonly row_span: number;
  readonly col_span: number;
  readonly page: number;
  readonly text: string;
  readonly value_type: ValueType;
  readonly value_number: string | null;
  readonly value_date: string | null;
  readonly confidence: number;
  readonly base_confidence: number;
  readonly flags: readonly CellFlag[];
  readonly original_text: string | null;
  readonly bbox: Readonly<Record<string, number>> | null;
}

export interface TableValidationRow {
  readonly kind: CheckKind;
  readonly scope: "RELATION" | "CELL";
  readonly outcome: "PASS" | "FAIL";
  readonly row_index: number | null;
  readonly col_index: number | null;
  readonly expected: string | null;
  readonly actual: string | null;
  readonly checked: number;
  readonly failed: number;
  readonly message: string;
  readonly detail: Readonly<Record<string, unknown>>;
}

export interface TableSummary {
  readonly id: string;
  readonly work_item_id: string;
  readonly original_filename: string;
  readonly ordinal: number;
  readonly title: string | null;
  readonly page_start: number;
  readonly page_end: number;
  readonly n_rows: number;
  readonly n_cols: number;
  readonly header_rows: number;
  readonly method: TableMethod;
  readonly rotation: number;
  readonly skew_degrees: number;
  readonly confidence: number;
  readonly status: TableStatus;
  readonly failed_checks: number;
  readonly checked_relations: number;
  readonly layout_key: string;
  readonly revision: number;
  readonly created_at: string;
  readonly corrected_at: string | null;
  readonly reviewed_at: string | null;
}

export interface TableList {
  readonly items: readonly TableSummary[];
  readonly total: number;
  readonly counts_by_status: Readonly<Record<string, number>>;
}

export interface LearnedMapping {
  readonly header_key: string;
  readonly role: ColumnRole;
  readonly confirmations: number;
  readonly contradictions: number;
  readonly applied: boolean;
}

export interface TableDetail {
  readonly table: TableSummary;
  readonly columns: readonly TableColumn[];
  readonly rows: readonly TableRowInfo[];
  readonly cells: readonly TableCell[];
  readonly validations: readonly TableValidationRow[];
  readonly learned_mappings: readonly LearnedMapping[];
}

export interface DocumentTables {
  readonly work_item_id: string;
  readonly original_filename: string;
  readonly page_count: number | null;
  readonly extractable: boolean;
  readonly tables: readonly TableSummary[];
}

export interface ExtractResult {
  readonly ran: "SYNC" | "QUEUED" | "SKIPPED";
  readonly reason: string | null;
  readonly tables: readonly TableSummary[];
}

export interface CellEdit {
  readonly row: number;
  readonly col: number;
  readonly text: string;
}

/** Confidence heat: the background a cell gets for its confidence (0..1). */
export const confidenceTone = (confidence: number): string => {
  if (confidence >= 0.95) {return "";}
  if (confidence >= 0.85) {return "bg-amber-500/10";}
  if (confidence >= 0.6) {return "bg-amber-500/25";}
  return "bg-red-500/25";
};
