/**
 * ARCH-17 — per-tenant SLO Data Transfer Objects.
 */

export type SLOUnit = "MILLISECONDS" | "RATIO";

export type SLOWindow = "HOUR" | "DAY" | "MONTH";

export type SLOMethod = "EXACT" | "HISTOGRAM_INTERPOLATED";

export type SLOSource = "ORGANIZATION" | "PLATFORM_DEFAULT" | "REGISTRY_DEFAULT";

export interface SLOTarget {
  readonly slo_key: string;
  readonly display_name: string;
  readonly description: string;
  readonly unit: SLOUnit;
  readonly target_value: string;
  readonly window_period: SLOWindow;
  readonly is_contractual: boolean;
  readonly source: SLOSource;
  readonly definition_id: string | null;
  readonly notes: string | null;
}

export interface SLOComplianceEntry {
  readonly slo_key: string;
  readonly target: SLOTarget;
  readonly observed_value: string | null;
  readonly sample_count: number;
  readonly error_count: number;
  readonly breached: boolean;
  readonly method: SLOMethod | null;
  readonly window_start: string;
  readonly window_end: string;
  readonly breached_windows: number;
  readonly total_windows: number;
  readonly compliance_ratio: string | null;
}

export interface SLOSummary {
  readonly organization_id: string;
  readonly as_of: string;
  readonly period: SLOWindow;
  readonly contractual_breaches: number;
  readonly entries: readonly SLOComplianceEntry[];
}

export interface SLOTargetUpdateRequest {
  readonly target_value: string;
  readonly window_period?: SLOWindow;
  readonly is_contractual: boolean;
  readonly notes?: string | null;
}

export const formatSLOValue = (
  value: string | null,
  unit: SLOUnit,
): string => {
  if (value === null) {return "—";}
  const numeric = Number(value);
  if (Number.isNaN(numeric)) {return "—";}
  if (unit === "RATIO") {return `${(numeric * 100).toFixed(2)}%`;}
  if (numeric >= 1000) {return `${(numeric / 1000).toFixed(2)}s`;}
  return `${Math.round(numeric)}ms`;
};

export const sloGaugeFraction = (entry: SLOComplianceEntry): number | null => {
  if (entry.observed_value === null) {return null;}
  const observed = Number(entry.observed_value);
  const target = Number(entry.target.target_value);
  if (!Number.isFinite(observed) || !Number.isFinite(target) || target === 0) {
    return null;
  }
  const fraction =
    entry.target.unit === "MILLISECONDS" ? observed / target : observed / target;
  return Math.max(0, Math.min(1, fraction));
};
