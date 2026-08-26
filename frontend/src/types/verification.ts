/**
 * ARCH-13 verification triage DTOs.
 * Mirrors `app/schemas/verification.py` and `app/models/verification.py`.
 */

export type VerificationStatus =
  | "PENDING"
  | "AGREED"
  | "DISAGREED"
  | "REVIEWED"
  | "AUTO_APPROVED";

export type DisagreementKind = "MISSING" | "CONFLICT" | "FORMAT";

export interface VerificationFieldResponse {
  readonly field_path: string;
  readonly agreed: boolean;
  readonly confidence: string;
  readonly consensus_value: unknown;
  readonly agent_values: readonly unknown[];
  readonly disagreement_kind: DisagreementKind | null;
  readonly resolved_value: unknown;
}

export interface VerificationSummaryResponse {
  readonly id: string;
  readonly work_item_id: string;
  readonly status: VerificationStatus;
  readonly agent_count: number;
  readonly agreement_score: string | null;
  readonly confidence: string | null;
  readonly cost_micros: number;
  readonly auto_approved: boolean;
  readonly reviewed_by_user_id: string | null;
  readonly reviewed_at: string | null;
  readonly created_at: string;
}

export interface VerificationDetailResponse
  extends VerificationSummaryResponse {
  readonly fields: readonly VerificationFieldResponse[];
  readonly details: Record<string, unknown>;
}

export interface VerificationResolveRequest {
  readonly values: Record<string, unknown>;
}

export interface VerificationListParams {
  readonly status?: VerificationStatus;
  readonly skip?: number;
  readonly limit?: number;
}

export const parseScore = (value: string | null | undefined): number | null => {
  if (value === null || value === undefined) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

export const formatFieldValue = (value: unknown): string => {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "string") {
    return value.length === 0 ? "(empty)" : value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "(unrenderable)";
  }
};

export const isBlocking = (status: VerificationStatus): boolean =>
  status === "PENDING" || status === "DISAGREED";
