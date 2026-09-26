/**
 * ARCH47-S2:types — ERP & System-of-Record Posting. Mirrors
 * backend/app/schemas/erp.py field for field (verify_arch47 W3 compares them)
 * and the vocabulary in backend/app/services/erp/vocabulary.py.
 */

export type ObjectKind = "VENDOR_BILL" | "PURCHASE_ORDER" | "GOODS_RECEIPT" | "JOURNAL_ENTRY" | "PAYMENT_REFERENCE";
export type SourceKind = "PROCUREMENT_CASE" | "TABLE" | "CASE";
export type PostingFormat = "CSV" | "XLSX" | "X12" | "UBL" | "TALLY" | "JSON";
export type PostingTransport = "DOWNLOAD" | "SFTP" | "HTTP";
export type PostingPreset =
  | "NONE" | "QUICKBOOKS_ONLINE" | "ZOHO_BOOKS" | "BUSINESS_CENTRAL" | "S4HANA_ODATA" | "NETSUITE_REST"
  | "GENERIC_REST" | "GENERIC_ODATA";
export type AckMode = "SYNC" | "X12_997" | "ACK_FILE" | "DELIVERY" | "MANUAL";
export type AuthMode =
  | "NONE" | "BEARER" | "BASIC" | "API_KEY_HEADER" | "OAUTH2_REFRESH_TOKEN" | "OAUTH2_CLIENT_CREDENTIALS"
  | "SSH_PASSWORD" | "SSH_KEY";
export type TargetStatus = "ACTIVE" | "DISABLED";
export type MappingStatus = "ACTIVE" | "RETIRED";
export type PostingState =
  | "PENDING" | "SENDING" | "RETRYING" | "DELIVERED" | "DONE" | "FAILED" | "REJECTED" | "MISMATCH" | "UNCERTAIN"
  | "CANCELLED";
export type PostingOrigin = "MANUAL" | "AUTO" | "FLOW";
export type AttemptKind = "RENDER" | "SEND" | "PROBE" | "ACK" | "REVIEW";
export type AttemptOutcome =
  | "OK" | "TRANSIENT" | "PERMANENT" | "UNCERTAIN" | "FOUND" | "ABSENT" | "PENDING" | "ACCEPTED" | "REJECTED"
  | "MISMATCH";
export type PostingVerdict = "RETRY" | "ACCEPT" | "CANCEL";

export const OBJECT_KINDS: readonly ObjectKind[] = [
  "VENDOR_BILL", "PURCHASE_ORDER", "GOODS_RECEIPT", "JOURNAL_ENTRY", "PAYMENT_REFERENCE",
];
export const SOURCE_KINDS: readonly SourceKind[] = ["PROCUREMENT_CASE", "TABLE", "CASE"];
export const POSTING_STATES: readonly PostingState[] = [
  "PENDING", "SENDING", "RETRYING", "DELIVERED", "DONE", "FAILED", "REJECTED", "MISMATCH", "UNCERTAIN", "CANCELLED",
];
export const EXCEPTION_STATES: readonly PostingState[] = ["FAILED", "REJECTED", "MISMATCH", "UNCERTAIN"];
export const OPEN_STATES: readonly PostingState[] = ["PENDING", "SENDING", "RETRYING", "DELIVERED"];

export const OBJECT_LABELS: Readonly<Record<ObjectKind, string>> = {
  VENDOR_BILL: "Vendor bill",
  PURCHASE_ORDER: "Purchase order",
  GOODS_RECEIPT: "Goods receipt",
  JOURNAL_ENTRY: "Journal entry",
  PAYMENT_REFERENCE: "Payment reference",
};

export const SOURCE_LABELS: Readonly<Record<SourceKind, string>> = {
  PROCUREMENT_CASE: "Reconciled invoice",
  TABLE: "Confirmed table",
  CASE: "Completed case",
};

export const STATE_LABELS: Readonly<Record<PostingState, string>> = {
  PENDING: "Queued",
  SENDING: "Sending",
  RETRYING: "Retrying",
  DELIVERED: "Awaiting acknowledgement",
  DONE: "Posted",
  FAILED: "Failed",
  REJECTED: "Rejected",
  MISMATCH: "Acknowledgement mismatch",
  UNCERTAIN: "Outcome unknown",
  CANCELLED: "Cancelled",
};

export const STATE_TONES: Readonly<Record<PostingState, string>> = {
  PENDING: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  SENDING: "bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300",
  RETRYING: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  DELIVERED: "bg-indigo-100 text-indigo-800 dark:bg-indigo-900/40 dark:text-indigo-300",
  DONE: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  FAILED: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  REJECTED: "bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300",
  MISMATCH: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
  UNCERTAIN: "bg-fuchsia-100 text-fuchsia-800 dark:bg-fuchsia-900/40 dark:text-fuchsia-300",
  CANCELLED: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
};

export const FORMAT_LABELS: Readonly<Record<PostingFormat, string>> = {
  CSV: "CSV (RFC 4180)",
  XLSX: "Excel workbook (.xlsx)",
  X12: "EDI X12 004010 (810 / 850 / 856)",
  UBL: "UBL 2.1 XML",
  TALLY: "Tally Prime XML",
  JSON: "REST / OData JSON",
};

export const PRESET_LABELS: Readonly<Record<PostingPreset, string>> = {
  NONE: "None",
  QUICKBOOKS_ONLINE: "QuickBooks Online",
  ZOHO_BOOKS: "Zoho Books",
  BUSINESS_CENTRAL: "Dynamics 365 Business Central",
  S4HANA_ODATA: "SAP S/4HANA (OData V2)",
  NETSUITE_REST: "Oracle NetSuite (REST)",
  GENERIC_REST: "Generic REST",
  GENERIC_ODATA: "Generic OData V4",
};

export const TRANSPORT_LABELS: Readonly<Record<PostingTransport, string>> = {
  DOWNLOAD: "Download (import by hand)",
  SFTP: "SFTP upload",
  HTTP: "HTTPS API",
};

export const ACK_LABELS: Readonly<Record<AckMode, string>> = {
  SYNC: "The API's response",
  X12_997: "X12 997 functional acknowledgement",
  ACK_FILE: "Acknowledgement file (.ack / Tally response)",
  DELIVERY: "Delivery only (file verified on the server)",
  MANUAL: "A person confirms the import",
};

export const AUTH_LABELS: Readonly<Record<AuthMode, string>> = {
  NONE: "None",
  BEARER: "Bearer token",
  BASIC: "Basic (username + password)",
  API_KEY_HEADER: "API key header",
  OAUTH2_REFRESH_TOKEN: "OAuth 2.0 refresh token",
  OAUTH2_CLIENT_CREDENTIALS: "OAuth 2.0 client credentials",
  SSH_PASSWORD: "SSH password",
  SSH_KEY: "SSH private key",
};

/** The fields each auth mode's credential takes (backend/app/services/erp/service.py _CREDENTIAL_FIELDS). */
export const CREDENTIAL_FIELDS: Readonly<Record<AuthMode, { required: readonly string[]; optional: readonly string[] }>> = {
  NONE: { required: [], optional: [] },
  BEARER: { required: ["token"], optional: [] },
  BASIC: { required: ["username", "password"], optional: [] },
  API_KEY_HEADER: { required: ["value"], optional: ["header"] },
  OAUTH2_REFRESH_TOKEN: { required: ["client_id", "client_secret", "refresh_token"], optional: [] },
  OAUTH2_CLIENT_CREDENTIALS: { required: ["client_id", "client_secret"], optional: [] },
  SSH_PASSWORD: { required: ["username", "password"], optional: [] },
  SSH_KEY: { required: ["username", "private_key"], optional: ["passphrase"] },
};

export const SECRET_FIELDS: ReadonlySet<string> = new Set(["token", "password", "value", "client_secret", "refresh_token",
  "private_key", "passphrase"]);

// -- catalog ------------------------------------------------------------------------------

export interface PresetInfo {
  readonly key: PostingPreset;
  readonly label: string;
  readonly auth_modes: readonly AuthMode[];
  readonly config_keys: readonly string[];
  readonly objects: readonly ObjectKind[];
  readonly idempotency?: string | null;
  readonly probe_objects: readonly ObjectKind[];
}

export interface FormatInfo {
  readonly key: PostingFormat;
  readonly label: string;
  readonly transports: readonly PostingTransport[];
  readonly objects: readonly ObjectKind[];
}

export interface ErpCatalog {
  readonly formats: readonly FormatInfo[];
  readonly transports: readonly PostingTransport[];
  readonly presets: readonly PresetInfo[];
  readonly ack_modes: Readonly<Record<string, readonly AckMode[]>>;
  readonly auth_modes: Readonly<Record<string, readonly AuthMode[]>>;
  readonly object_kinds: readonly ObjectKind[];
  readonly object_labels: Readonly<Record<string, string>>;
  readonly source_kinds: readonly SourceKind[];
  readonly source_labels: Readonly<Record<string, string>>;
  readonly states: readonly PostingState[];
  readonly state_labels: Readonly<Record<string, string>>;
  readonly transforms: readonly string[];
  readonly header_paths: Readonly<Record<string, string>>;
  readonly line_paths: Readonly<Record<string, string>>;
  readonly language: string;
}

// -- targets --------------------------------------------------------------------------------

export interface TargetCreate {
  readonly name: string;
  readonly format: PostingFormat;
  readonly transport: PostingTransport;
  readonly preset?: PostingPreset;
  readonly ack_mode?: AckMode | null;
  readonly auth_mode?: AuthMode | null;
  readonly config?: Record<string, unknown>;
  readonly credential?: Record<string, string> | null;
  readonly lookup_prefix?: string | null;
  readonly auto_post?: boolean;
  readonly auto_sources?: readonly SourceKind[];
  readonly auto_objects?: readonly ObjectKind[];
  readonly max_attempts?: number | null;
  readonly ack_timeout_hours?: number | null;
}

export interface TargetUpdate {
  readonly name?: string | null;
  readonly status?: TargetStatus | null;
  readonly ack_mode?: AckMode | null;
  readonly auth_mode?: AuthMode | null;
  readonly config?: Record<string, unknown> | null;
  readonly auto_post?: boolean | null;
  readonly auto_sources?: readonly SourceKind[] | null;
  readonly auto_objects?: readonly ObjectKind[] | null;
  readonly max_attempts?: number | null;
  readonly ack_timeout_hours?: number | null;
}

export interface CredentialSet {
  readonly credential: Record<string, string>;
}

export interface TargetRow {
  readonly id: string;
  readonly name: string;
  readonly format: PostingFormat;
  readonly transport: PostingTransport;
  readonly preset: PostingPreset;
  readonly ack_mode: AckMode;
  readonly auth_mode: AuthMode;
  readonly status: TargetStatus;
  readonly host?: string | null;
  readonly credential_set: boolean;
  readonly credential_fingerprint?: string | null;
  readonly credential_updated_at?: string | null;
  readonly auto_post: boolean;
  readonly auto_post_since?: string | null;
  readonly auto_sources: readonly SourceKind[];
  readonly auto_objects: readonly ObjectKind[];
  readonly max_attempts: number;
  readonly ack_timeout_hours: number;
  readonly objects: readonly ObjectKind[];
  readonly lookup_prefix?: string | null;
  readonly postings: number;
  readonly open_exceptions: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface TargetList {
  readonly items: readonly TargetRow[];
}

export interface MappingRow {
  readonly id: string;
  readonly object_kind: ObjectKind;
  readonly version: number;
  readonly status: MappingStatus;
  readonly spec: Record<string, unknown>;
  readonly spec_sha: string;
  readonly note?: string | null;
  readonly created_at: string;
  readonly retired_at?: string | null;
}

export interface TargetDetail {
  readonly target: TargetRow;
  readonly config: Record<string, unknown>;
  readonly mappings: readonly MappingRow[];
  readonly lookup_tables: readonly string[];
}

export interface TestResult {
  readonly ok: boolean;
  readonly kind: string;
  readonly message: string;
  readonly detail: Record<string, unknown>;
}

// -- mappings ---------------------------------------------------------------------------------

export interface MappingSave {
  readonly spec: Record<string, unknown>;
  readonly note?: string | null;
}

export interface MappingVersions {
  readonly target_id: string;
  readonly object_kind: ObjectKind;
  readonly active?: MappingRow | null;
  readonly versions: readonly MappingRow[];
  readonly default_spec: Record<string, unknown>;
}

export interface MappingCheck {
  readonly valid: boolean;
  readonly problems: readonly string[];
}

// -- lookup tables ------------------------------------------------------------------------------

export interface LookupCreate {
  readonly name: string;
  readonly description?: string | null;
  readonly entries?: Record<string, string>;
}

export interface LookupUpdate {
  readonly description?: string | null;
  readonly entries?: Record<string, string> | null;
  readonly merge?: boolean;
}

export interface LookupRow {
  readonly id: string;
  readonly name: string;
  readonly description?: string | null;
  readonly entries: Readonly<Record<string, string>>;
  readonly entry_count: number;
  readonly revision: number;
  readonly used_by: readonly string[];
  readonly updated_at: string;
}

export interface LookupList {
  readonly items: readonly LookupRow[];
}

// -- postings -------------------------------------------------------------------------------------

export interface PostRequest {
  readonly target_id: string;
  readonly source_kind: SourceKind;
  readonly source_id: string;
  readonly object_kinds: readonly ObjectKind[];
}

export interface PreviewRequest {
  readonly target_id: string;
  readonly source_kind: SourceKind;
  readonly source_id: string;
  readonly object_kind: ObjectKind;
  readonly spec?: Record<string, unknown> | null;
}

export interface ReviewAction {
  readonly note?: string | null;
  readonly reference?: string | null;
}

export interface AcknowledgeRequest {
  readonly accepted?: boolean | null;
  readonly reference?: string | null;
  readonly reason?: string | null;
  readonly response_text?: string | null;
}

export interface PostingRow {
  readonly id: string;
  readonly target_id: string;
  readonly target_name: string;
  readonly target_format: PostingFormat;
  readonly target_preset: PostingPreset;
  readonly object_kind: ObjectKind;
  readonly source_kind: SourceKind;
  readonly source_id: string;
  readonly work_item_id?: string | null;
  readonly work_item_filename?: string | null;
  readonly origin: PostingOrigin;
  readonly state: PostingState;
  readonly document_number?: string | null;
  readonly amount?: string | null;
  readonly currency?: string | null;
  readonly attempts: number;
  readonly max_attempts: number;
  readonly next_attempt_at?: string | null;
  readonly external_id?: string | null;
  readonly last_error?: string | null;
  readonly mapping_version?: number | null;
  readonly rendered_filename?: string | null;
  readonly content_sha?: string | null;
  readonly delivered_at?: string | null;
  readonly acknowledged_at?: string | null;
  readonly reviewed_at?: string | null;
  readonly erased: boolean;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface PostingList {
  readonly items: readonly PostingRow[];
  readonly total: number;
  readonly counts_by_state: Readonly<Record<string, number>>;
}

export interface AttemptRow {
  readonly seq: number;
  readonly kind: AttemptKind;
  readonly outcome: AttemptOutcome;
  readonly http_status?: number | null;
  readonly message?: string | null;
  readonly detail: Record<string, unknown>;
  readonly actor_user_id?: string | null;
  readonly started_at: string;
}

export interface PostingDetail {
  readonly posting: PostingRow;
  readonly canonical?: Record<string, unknown> | null;
  readonly mapped?: Record<string, unknown> | null;
  readonly rendered_preview?: string | null;
  readonly rendered_media_type?: string | null;
  readonly rendered_size?: number | null;
  readonly ack: Record<string, unknown>;
  readonly control_numbers: Record<string, unknown>;
  readonly remote_path?: string | null;
  readonly idempotency_key: string;
  readonly source_label?: string | null;
  readonly source_changed?: boolean | null;
  readonly attempts: readonly AttemptRow[];
  readonly review_note?: string | null;
}

export interface PostResult {
  readonly object_kind: ObjectKind;
  readonly posting_id?: string | null;
  readonly created: boolean;
  readonly state?: PostingState | null;
  readonly error?: string | null;
}

export interface PostResponse {
  readonly results: readonly PostResult[];
}

export interface PreviewResponse {
  readonly ok: boolean;
  readonly canonical?: Record<string, unknown> | null;
  readonly mapped?: Record<string, unknown> | null;
  readonly rendered_preview?: string | null;
  readonly rendered_media_type?: string | null;
  readonly filename?: string | null;
  readonly problems: readonly string[];
  readonly notes: readonly string[];
}

export interface OutcomePosting {
  readonly target_id: string;
  readonly object_kind: ObjectKind;
  readonly posting_id?: string | null;
  readonly state?: PostingState | null;
}

export interface OutcomeRow {
  readonly kind: SourceKind;
  readonly id: string;
  readonly label: string;
  readonly approved_at?: string | null;
  readonly work_item_id?: string | null;
  readonly documents: Readonly<Record<string, string>>;
  readonly objects: readonly ObjectKind[];
  readonly postings: readonly OutcomePosting[];
}

export interface OutcomeList {
  readonly items: readonly OutcomeRow[];
}

// -- helpers ----------------------------------------------------------------------------------------

export const isException = (state: PostingState): boolean => EXCEPTION_STATES.includes(state);

/** What a reviewer may decide for a posting in this state (mirrors service.review). */
export const verdictsFor = (state: PostingState): readonly PostingVerdict[] => {
  if (EXCEPTION_STATES.includes(state)) {
    return ["RETRY", "ACCEPT", "CANCEL"];
  }
  if (state === "RETRYING") {
    return ["RETRY", "CANCEL"];
  }
  if (state === "PENDING" || state === "DELIVERED") {
    return ["CANCEL"];
  }
  return [];
};

/** A starting configuration for a new target (the server validates every key). */
export const configTemplate = (format: PostingFormat, transport: PostingTransport, preset: PostingPreset): Record<string, unknown> => {
  const cfg: Record<string, unknown> = {};
  if (format === "CSV") {
    cfg.csv = { delimiter: "," };
  }
  if (format === "TALLY") {
    cfg.tally = { company: "" };
  }
  if (format === "X12") {
    cfg.x12 = { sender_qualifier: "ZZ", sender_id: "YOURSENDERID", receiver_qualifier: "ZZ", receiver_id: "PARTNERID", usage: "T" };
  }
  if (transport === "SFTP") {
    cfg.sftp = { host: "", port: 22, host_key_sha256: "SHA256:", directory: "/inbound" };
  }
  if (transport === "HTTP") {
    cfg.http = { base_url: "https://" };
    if (preset === "QUICKBOOKS_ONLINE") {
      cfg.qbo = { realm_id: "" };
    }
    if (preset === "ZOHO_BOOKS") {
      cfg.zoho = { organization_id: "" };
    }
    if (preset === "BUSINESS_CENTRAL") {
      cfg.bc = { company_id: "" };
    }
    if (preset === "S4HANA_ODATA") {
      cfg.s4 = { sap_client: "" };
    }
    if (preset === "GENERIC_REST" || preset === "GENERIC_ODATA") {
      cfg.rest = { paths: { VENDOR_BILL: "/bills" } };
    }
  }
  return cfg;
};
