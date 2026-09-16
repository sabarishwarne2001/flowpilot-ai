/**
 * ARCH-35 — calibrated autonomy DTOs.
 * Mirrors `app/schemas/calibration.py`.
 *
 * Every rate, bound and threshold is a Decimal STRING on the wire. The one
 * place a string becomes a number for display is `toNumber` below, so no
 * component does arithmetic on a string by accident.
 */

export type CalibrationMethod = "ISOTONIC" | "PLATT" | "PRIOR";

export type CalibrationStatus = "ACTIVE" | "SUPERSEDED" | "SUSPENDED" | "REJECTED";

export interface AutonomyEntry {
  readonly decision_type: string;
  readonly display_name: string;
  readonly automated: boolean;
  readonly label_count: number;
  readonly model_id: string | null;
  readonly method: CalibrationMethod | null;
  readonly status: CalibrationStatus | null;
  readonly target_error_rate: string;
  readonly audit_sample_rate: string;
  readonly threshold: string | null;
  readonly conformal_bound: string | null;
  readonly clopper_pearson_upper: string | null;
  readonly auto_share: string | null;
  readonly ece_before: string | null;
  readonly ece_after: string | null;
  readonly brier_after: string | null;
  readonly achievable: boolean;
  readonly stale: boolean;
  readonly fitted_at: string | null;
  readonly last_checked_at: string | null;
  readonly suspended_at: string | null;
  readonly suspended_reason: string | null;
  readonly last_rejection: string | null;
  readonly summary: string;
}

export interface AutonomyOverview {
  readonly organization_id: string;
  readonly as_of: string;
  readonly min_labels: number;
  readonly min_labels_isotonic: number;
  readonly confidence: number;
  readonly entries: readonly AutonomyEntry[];
}

export interface ReliabilityBin {
  readonly lower: number;
  readonly upper: number;
  readonly count: number;
  readonly mean_predicted: number | null;
  readonly mean_raw: number | null;
  readonly observed: number | null;
}

export interface FittedCurvePoint {
  readonly score: number;
  readonly probability: number;
}

export interface CoveragePoint {
  readonly threshold: number;
  readonly auto_share: number;
  readonly conformal_bound: number;
  readonly clopper_pearson_upper: number;
  readonly observed_error: number;
  readonly passed: number;
}

export interface MonitorReading {
  readonly suspend: boolean;
  readonly reason: string;
  readonly psi: number | null;
  readonly psi_checked: boolean;
  readonly realized_wrong: number;
  readonly realized_total: number;
  readonly realized_p_value: number;
  readonly notes: readonly string[];
  readonly at: string;
}

export interface AutonomyReliability {
  readonly entry: AutonomyEntry;
  readonly holdout_count: number;
  readonly audit_labels: number;
  readonly reliability: readonly ReliabilityBin[];
  readonly reliability_raw: readonly ReliabilityBin[];
  readonly fitted_curve: readonly FittedCurvePoint[];
  readonly coverage_curve: readonly CoveragePoint[];
  readonly last_check: MonitorReading | null;
  readonly psi_threshold: number;
  readonly confidence: number;
  readonly min_labels: number;
  readonly min_labels_isotonic: number;
  readonly target_error_rate_max: string;
  readonly audit_sample_rate_min: string;
  readonly audit_sample_rate_max: string;
}

export interface AutonomySettingsRequest {
  readonly target_error_rate: string;
  readonly audit_sample_rate: string;
}

export interface AutonomyActionResult {
  readonly ok: boolean;
  readonly outcome: string;
  readonly message: string;
  readonly entry: AutonomyEntry;
}

export const CALIBRATED_AUTONOMY_CAPABILITY = "capability.calibrated_autonomy";

export const METHOD_LABELS: Readonly<Record<CalibrationMethod, string>> = {
  ISOTONIC: "Isotonic regression",
  PLATT: "Platt scaling",
  PRIOR: "Not enough reviews yet",
};

export const toNumber = (value: string | null | undefined): number | null => {
  if (value === null || value === undefined) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

/** "0.4%", "12%", "—". One decimal below 10%, none above. */
export const formatRate = (value: number | string | null | undefined): string => {
  const number = typeof value === "string" ? toNumber(value) : value;
  if (number === null || number === undefined) {
    return "—";
  }
  const percent = number * 100;
  if (percent >= 10 || Number.isInteger(percent)) {
    return `${Math.round(percent)}%`;
  }
  return `${percent.toFixed(1)}%`;
};

export interface SliderOutcome {
  readonly achievable: boolean;
  readonly point: CoveragePoint | null;
  readonly autoShare: number;
  /** Share of all documents a person reviews, audits included. */
  readonly reviewShare: number;
}

/**
 * The smallest threshold whose conformal bound is within α, read from the
 * curve the server computed. The curve runs from the highest threshold down,
 * and the bound only grows along it, so the last point still within α is the
 * smallest threshold that keeps the promise. Every point is exact; thinning
 * on the server can only make this answer slightly conservative, never wrong.
 */
export const outcomeForAlpha = (
  curve: readonly CoveragePoint[],
  alpha: number,
  auditRate: number,
): SliderOutcome => {
  let chosen: CoveragePoint | null = null;
  for (const point of curve) {
    if (point.conformal_bound <= alpha + 1e-12) {
      chosen = point;
    } else {
      break;
    }
  }
  if (chosen === null) {
    return { achievable: false, point: null, autoShare: 0, reviewShare: 1 };
  }
  const autoShare = chosen.auto_share;
  return {
    achievable: autoShare > 0,
    point: chosen,
    autoShare,
    reviewShare: Math.min(1, 1 - autoShare + autoShare * auditRate),
  };
};
