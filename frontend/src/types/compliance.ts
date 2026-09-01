/**
 * ARCH-20 — data governance, residency and compliance DTOs.
 *
 * Note what is absent from ComplianceExport: a download URL. The backend
 * persists a storage key and mints a presigned URL per request from a
 * dedicated endpoint, so a link is never stale and never sits in a cached
 * list payload. `getComplianceExportDownloadUrl` is the only way to get one.
 */

export type DataResidencyRegion = "US" | "EU" | "APAC" | "GLOBAL";

export type ComplianceExportStatus =
  | "PENDING"
  | "RUNNING"
  | "COMPLETE"
  | "FAILED"
  | "EXPIRED";

export interface ResidencyRegionOption {
  readonly region: DataResidencyRegion;
  /**
   * False when this deployment has no bucket for the region. The option is
   * still listed, because hiding it makes an unconfigured region look like an
   * unsupported one — but it is not selectable.
   */
  readonly configured: boolean;
}

export interface DataResidency {
  readonly region: DataResidencyRegion;
  readonly available_regions: readonly ResidencyRegionOption[];
}

export interface DataResidencyUpdateRequest {
  readonly region: DataResidencyRegion;
  readonly acknowledge_no_migration: boolean;
}

export interface RetentionPolicy {
  readonly organization_id: string;
  readonly work_item_retention_days: number | null;
  readonly audit_retention_days: number | null;
  readonly conversation_retention_days: number | null;
  readonly auto_purge_enabled: boolean;
  readonly audit_retention_floor_days: number;
  readonly updated_at: string | null;
}

export interface RetentionPolicyUpdateRequest {
  readonly work_item_retention_days: number | null;
  readonly audit_retention_days: number | null;
  readonly conversation_retention_days: number | null;
  readonly auto_purge_enabled: boolean;
}

export interface ErasureRequestPayload {
  readonly subject_user_id: string;
  readonly erasure_ticket: string;
  readonly confirm_subject_email: string;
}

export interface ErasurePreview {
  readonly subject_user_id: string;
  readonly counts: Readonly<Record<string, number>>;
  readonly preserved_tables: readonly string[];
}

export interface ErasedSubject {
  readonly id: string;
  readonly organization_id: string;
  readonly subject_user_id: string | null;
  readonly subject_email_hash: string;
  readonly erasure_ticket: string;
  readonly erased_by_user_id: string | null;
  readonly erased_at: string;
  readonly details: Readonly<Record<string, unknown>> | null;
}

export interface ErasureResult {
  readonly erased_subject: ErasedSubject;
  readonly already_erased: boolean;
  readonly counts: Readonly<Record<string, number>>;
}

export interface ComplianceExport {
  readonly id: string;
  readonly organization_id: string;
  readonly requested_by_user_id: string | null;
  readonly status: ComplianceExportStatus;
  readonly residency_region: DataResidencyRegion;
  readonly file_size_bytes: number | null;
  readonly error_message: string | null;
  readonly expires_at: string | null;
  readonly created_at: string;
  readonly completed_at: string | null;
}

export interface ComplianceExportDownload {
  readonly export_id: string;
  readonly download_url: string;
  readonly expires_in_seconds: number;
}

export interface ComplianceOverview {
  readonly organization_id: string;
  readonly residency: DataResidency;
  readonly retention: RetentionPolicy;
  readonly erasure_count: number;
  readonly export_count: number;
  readonly latest_export: ComplianceExport | null;
}

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------

export const REGION_LABELS: Readonly<Record<DataResidencyRegion, string>> = {
  US: "United States",
  EU: "European Union",
  APAC: "Asia Pacific",
  GLOBAL: "Global (no residency guarantee)",
};

export const REGION_DESCRIPTIONS: Readonly<
  Record<DataResidencyRegion, string>
> = {
  US: "Objects are written to a US-pinned bucket.",
  EU: "Objects are written to an EU-pinned bucket. Required by most GDPR data processing agreements.",
  APAC: "Objects are written to an Asia Pacific bucket.",
  GLOBAL:
    "The platform default bucket. Choose a pinned region if your contract requires one.",
};

export const isDownloadable = (record: ComplianceExport): boolean => {
  if (record.status !== "COMPLETE") {
    return false;
  }
  if (!record.expires_at) {
    return true;
  }
  return new Date(record.expires_at).getTime() > Date.now();
};

export const formatBytes = (bytes: number | null): string => {
  if (bytes === null || bytes < 0) {
    return "—";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(1)} ${units[index]}`;
};

/**
 * Short hash prefix for the tombstone table. The full 64 characters carry no
 * extra meaning to a reader and make the column unreadable.
 */
export const shortHash = (hash: string): string => hash.slice(0, 12);

export const totalDestroyed = (
  counts: Readonly<Record<string, number>>,
): number => Object.values(counts).reduce((sum, value) => sum + value, 0);
