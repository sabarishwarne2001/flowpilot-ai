/**
 * ARCH-26 — Enterprise Analytics, BI Egress & Warehouse Sync.
 *
 * WHAT IS DELIBERATELY ABSENT FROM THIS FILE
 * ==========================================
 *
 * There is no `costBasisMicros`, no `marginMicros`, and no field that could
 * carry one. That is invariant I1 reaching the frontend: supplier cost is
 * withheld from every tenant-facing surface, and a type that could hold it is
 * an invitation for a future endpoint to start sending it.
 *
 * There is also no secret. `WarehouseDestination` carries a
 * `credentialFingerprint` and nothing else from the credential — the request
 * types below can hold a private key on its way out, and no response type can
 * hold one on the way back. That asymmetry is invariant I2, and it is the
 * reason the request and response shapes here do not share a base interface.
 *
 * WHY `isDispatchable` IS A FIELD AND NOT A GETTER
 * ================================================
 *
 * The console could compute `enabled && !circuitOpenedAt`. It does not. The
 * backend sends it, on the ARCH-24 rule that the backend owns a threshold: a
 * control the frontend enables and the server then refuses reads as a bug
 * rather than as a policy.
 *
 * WHY THE NULLABLE COUNTERS ARE `number | null` AND NOT `number`
 * ==============================================================
 *
 * `rowCount` is null on a run that died before counting. Typing it `number`
 * would push a `?? 0` into every render site, and a zero row count renders
 * identically to an empty window — which is exactly the distinction someone
 * reading the run history is trying to make.
 */

export type DestinationKind = "SNOWFLAKE" | "BIGQUERY" | "DATABRICKS" | "S3";
export type DestinationStatus = "ACTIVE" | "DISABLED";
export type ExportDataset =
  | "USAGE_ROLLUPS"
  | "DOCUMENT_METADATA"
  | "ASSISTANT_TURNS"
  | "AUTOMATION_RUNS";
export type ScheduleCadence = "DAILY" | "WEEKLY" | "MONTHLY";
export type SyncTrigger = "SCHEDULED" | "MANUAL";
export type SyncStatus = "RUNNING" | "SUCCEEDED" | "PARTIAL" | "FAILED";

/** Ordering for every list and select in the console. */
export const DESTINATION_KINDS: readonly DestinationKind[] = [
  "SNOWFLAKE",
  "BIGQUERY",
  "DATABRICKS",
  "S3",
] as const;

export const EXPORT_DATASETS: readonly ExportDataset[] = [
  "USAGE_ROLLUPS",
  "DOCUMENT_METADATA",
  "ASSISTANT_TURNS",
  "AUTOMATION_RUNS",
] as const;

export const SCHEDULE_CADENCES: readonly ScheduleCadence[] = [
  "DAILY",
  "WEEKLY",
  "MONTHLY",
] as const;

export const KIND_LABELS: Record<DestinationKind, string> = {
  SNOWFLAKE: "Snowflake",
  BIGQUERY: "Google BigQuery",
  DATABRICKS: "Databricks",
  S3: "Amazon S3 / S3-compatible",
};

export const DATASET_LABELS: Record<ExportDataset, string> = {
  USAGE_ROLLUPS: "Usage rollups",
  DOCUMENT_METADATA: "Document metadata",
  ASSISTANT_TURNS: "Assistant turns",
  AUTOMATION_RUNS: "Automation runs",
};

export const DATASET_HINTS: Record<ExportDataset, string> = {
  USAGE_ROLLUPS:
    "Consumption quantities and the price you were invoiced. Supplier cost is not included.",
  DOCUMENT_METADATA:
    "One row per uploaded document — filename, size, checksum, timestamps. No file contents.",
  ASSISTANT_TURNS:
    "Message metadata only. Conversation text stays inside your configured residency boundary.",
  AUTOMATION_RUNS:
    "One row per automation execution, with node and action counters.",
};

export const STATUS_LABELS: Record<SyncStatus, string> = {
  RUNNING: "Running",
  SUCCEEDED: "Delivered",
  PARTIAL: "Partially delivered",
  FAILED: "Failed",
};

export const STATUS_CLASSES: Record<SyncStatus, string> = {
  RUNNING: "bg-blue-50 text-blue-700 border-blue-200",
  SUCCEEDED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  // Amber, not red. A partial run delivered real rows into the warehouse and
  // reporting it as a failure invites a retry that duplicates them.
  PARTIAL: "bg-amber-50 text-amber-800 border-amber-200",
  FAILED: "bg-red-50 text-red-700 border-red-200",
};

/** Days a schedule may look back. Mirrors the backend CHECK constraint. */
export const MIN_LOOKBACK_DAYS = 1;
export const MAX_LOOKBACK_DAYS = 90;

/**
 * 28 and not 31, matching `ck_export_schedules_day_of_month_in_range`. A
 * schedule on the 31st does not run in February, and the tenant finds the
 * hole in a board deck rather than in a log.
 */
export const MAX_DAY_OF_MONTH = 28;

export const WEEKDAY_LABELS: readonly string[] = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

// ---------------------------------------------------------------------------
// Credential payloads — request side only.
//
// No response interface below extends any of these.
// ---------------------------------------------------------------------------

export interface SnowflakeCredentialInput {
  kind: "SNOWFLAKE";
  account: string;
  user: string;
  warehouse: string;
  database: string;
  db_schema?: string;
  role?: string | null;
  /** An external stage the tenant already created, pointing at stage_bucket. */
  stage_name: string;
  stage_bucket: string;
  stage_region: string;
  stage_prefix?: string;
  stage_endpoint_url?: string | null;
  table_prefix?: string;
  /** PKCS#8 PEM. The SQL REST API has no password path. */
  private_key: string;
  private_key_passphrase?: string | null;
  stage_access_key_id: string;
  stage_secret_access_key: string;
}

export interface BigQueryCredentialInput {
  kind: "BIGQUERY";
  project_id: string;
  dataset: string;
  location?: string;
  table_prefix?: string;
  service_account_json: string;
}

export interface DatabricksCredentialInput {
  kind: "DATABRICKS";
  host: string;
  warehouse_id: string;
  catalog?: string;
  db_schema?: string;
  volume: string;
  table_prefix?: string;
  access_token: string;
}

export interface S3CredentialInput {
  kind: "S3";
  bucket: string;
  region: string;
  prefix?: string;
  endpoint_url?: string | null;
  access_key_id: string;
  secret_access_key: string;
}

export type WarehouseCredentialInput =
  | SnowflakeCredentialInput
  | BigQueryCredentialInput
  | DatabricksCredentialInput
  | S3CredentialInput;

export interface WarehouseDestinationCreate {
  label: string;
  credential: WarehouseCredentialInput;
}

export interface WarehouseDestinationUpdate {
  label?: string;
  status?: DestinationStatus;
  /** All-or-nothing. There is no partial credential edit. */
  credential?: WarehouseCredentialInput;
}

// ---------------------------------------------------------------------------
// Response shapes — nothing below can carry a secret.
// ---------------------------------------------------------------------------

export interface WarehouseDestination {
  id: string;
  organization_id: string;
  label: string;
  kind: DestinationKind;
  status: DestinationStatus;
  /** Non-secret connection parameters, so a wrong bucket is visible. */
  config: Record<string, unknown>;
  /** First 12 hex of SHA-256 over the credential. Not reversible. */
  credential_fingerprint: string;
  last_tested_at: string | null;
  /** null = never probed. false = probed and refused. Not the same fact. */
  last_test_ok: boolean | null;
  last_test_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConnectionTestResult {
  ok: boolean;
  kind: DestinationKind;
  /** null when no round trip completed. Never 0. */
  latency_ms: number | null;
  detail: string | null;
  tested_at: string;
}

export interface ExportSchedule {
  id: string;
  organization_id: string;
  destination_id: string;
  destination_label: string | null;
  datasets: ExportDataset[];
  cadence: ScheduleCadence;
  hour_utc: number;
  day_of_week: number | null;
  day_of_month: number | null;
  lookback_days: number;
  enabled: boolean;
  consecutive_failure_count: number;
  circuit_opened_at: string | null;
  /** Computed by the backend. The console does not re-derive it. */
  is_dispatchable: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExportScheduleCreate {
  destination_id: string;
  datasets: ExportDataset[];
  cadence: ScheduleCadence;
  hour_utc: number;
  day_of_week?: number | null;
  day_of_month?: number | null;
  lookback_days: number;
  enabled: boolean;
}

export interface ExportScheduleUpdate {
  datasets?: ExportDataset[];
  cadence?: ScheduleCadence;
  hour_utc?: number;
  day_of_week?: number | null;
  day_of_month?: number | null;
  lookback_days?: number;
  enabled?: boolean;
  /**
   * Separate from `enabled` on purpose: "run this again" and "I fixed the
   * thing that was breaking it" are different claims, and only the second
   * should clear a failure count that was about to alert somebody.
   */
  reset_circuit?: boolean;
}

export interface ExportSyncRun {
  id: string;
  organization_id: string;
  destination_id: string | null;
  schedule_id: string | null;
  destination_label: string;
  destination_kind: DestinationKind;
  trigger: SyncTrigger;
  status: SyncStatus;
  datasets: string[];
  window_start: string;
  window_end: string;
  row_count: number | null;
  byte_count: number | null;
  part_count: number | null;
  bundle_digest: string | null;
  manifest_key: string | null;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  error_code: string | null;
  error_detail: string | null;
  attempt: number;
}

export interface ManualSyncRequest {
  destination_id: string;
  datasets: ExportDataset[];
  lookback_days: number;
}

export interface ManualSyncResponse {
  job_id: string;
  destination_id: string;
  datasets: string[];
  status: string;
}

export interface UsageDistributionBucket {
  event_type: string;
  bucket_start: string;
  quantity: number;
  /** What you were invoiced. There is no supplier-cost counterpart. */
  billed_micros: number;
  event_count: number;
}

export interface ConsumptionAnalytics {
  window_start: string;
  window_end: string;
  granularity: "HOUR" | "DAY" | "MONTH";
  buckets: UsageDistributionBucket[];
  total_billed_micros: number;
  total_event_count: number;
  /** null when no samples landed. A zero p95 claims instantaneous service. */
  p95_latency_ms: number | null;
  latency_method: "EXACT" | "HISTOGRAM_INTERPOLATED" | null;
  exportable_datasets: string[];
}

export interface ExportDatasetDescriptor {
  dataset: ExportDataset;
  version: number;
  description: string;
  columns: Array<{ name: string; type: string; description: string }>;
}

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------

/**
 * Micros to a currency string.
 *
 * Shared with the BYOK console's convention: micros are integers server-side
 * precisely so no rounding happens before display, and dividing here is the
 * only place it should.
 */
export const formatMicros = (micros: number | null): string => {
  if (micros === null || micros === undefined) {
    return "—";
  }
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(micros / 1_000_000);
};

/**
 * Render a count that may be unknown.
 *
 * The whole reason `rowCount` is nullable. "Not counted" and "zero rows" are
 * different outcomes and this is where that stays true on screen.
 */
export const formatCount = (value: number | null): string =>
  value === null || value === undefined ? "Not counted" : value.toLocaleString();

export const formatBytes = (value: number | null): string => {
  if (value === null || value === undefined) {
    return "—";
  }
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
};

export const formatDateTime = (value: string | null): string =>
  value ? new Date(value).toLocaleString() : "—";

/**
 * Describe a probe result in words, keeping null distinct from false.
 */
export const describeProbe = (destination: WarehouseDestination): string => {
  if (destination.last_test_ok === null) {
    return "Never tested";
  }
  if (destination.last_test_ok) {
    return `Reachable — ${formatDateTime(destination.last_tested_at)}`;
  }
  return destination.last_test_error ?? "Connection refused";
};

export const describeCadence = (schedule: ExportSchedule): string => {
  const at = `${String(schedule.hour_utc).padStart(2, "0")}:00 UTC`;
  if (schedule.cadence === "DAILY") {
    return `Daily at ${at}`;
  }
  if (schedule.cadence === "WEEKLY") {
    const day = WEEKDAY_LABELS[schedule.day_of_week ?? 0] ?? "Monday";
    return `Weekly on ${day} at ${at}`;
  }
  return `Monthly on day ${schedule.day_of_month ?? 1} at ${at}`;
};
