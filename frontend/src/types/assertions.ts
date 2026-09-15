/**
 * ARCH-33 — the assertion wire types.
 *
 * NUMBERS THAT MUST NOT BE `number`
 * =================================
 *
 * `threshold`, `rawScore` and `calibratedProbability` are strings, matching
 * the `Decimal` the backend serialises. 0.95 has no exact binary float, and a
 * slider that round-tripped through `number` would eventually post
 * 0.9499999999999999 — which `ck_ad_threshold_bounded` accepts and which is
 * not what the administrator set.
 *
 * The slider works in INTEGER PERCENT and converts once, at the edge. That is
 * the only arithmetic on a threshold anywhere in the console.
 */

export type AssertionFamily =
  | "duration_bound"
  | "notice_period"
  | "money_multiple_bound"
  | "money_bound"
  | "enumerated"
  | "presence"
  | "absence"
  | "llm";

export type AssertionVerdict = "PASS" | "FAIL" | "UNDETERMINED";

export type AssertionRoute = "PASS" | "TRIAGE";

export type EvaluationMode = "DETERMINISTIC" | "LLM";

export interface AssertionEvidence {
  readonly chunk_id: string | null;
  readonly chunk_index?: number | null;
  readonly page_number?: number | null;
  readonly quote: string;
  readonly span?: readonly number[] | null;
  readonly value?: string | null;
  readonly unit?: string | null;
  readonly literal?: string | null;
  readonly confidence?: number;
  readonly notes?: readonly string[];
  readonly source?: string;
}

export interface AssertionExtractedValue {
  readonly value?: string | null;
  readonly unit?: string | null;
  readonly literal?: string | null;
  readonly page_number?: number | null;
  readonly chunk_id?: string | null;
  readonly quote?: string | null;
  readonly subject?: string | null;
}

export interface AssertionPreview {
  readonly family: AssertionFamily;
  readonly evaluation_mode: EvaluationMode;
  /** §4.6's "Understood as:" line, already in plain words. */
  readonly understood_as: string;
  readonly requires_acknowledgement: boolean;
  readonly reason: string | null;
  readonly notes: readonly string[];
  readonly plan: Record<string, unknown>;
  readonly threshold: string;
  readonly effective_threshold: string;
  /** The sentence under the slider. Assembled by the backend, never here. */
  readonly consequence: string;
  readonly enough_labels: boolean;
  readonly label_count: number;
  readonly seed_phrases: readonly string[];
}

export interface AssertionDefinition {
  readonly id: string;
  readonly node_id: string;
  readonly sentence: string;
  readonly family: AssertionFamily;
  readonly evaluation_mode: EvaluationMode;
  readonly threshold: string;
  readonly version: number;
  readonly plan: Record<string, unknown>;
  readonly understood_as: string;
  readonly llm_acknowledged_by: string | null;
  readonly created_at: string | null;
}

export interface AssertionSimulation {
  readonly work_item_id: string;
  readonly verdict: AssertionVerdict;
  readonly routed_to: AssertionRoute;
  readonly edge: "pass" | "triage";
  readonly reason: string;
  readonly raw_score: string;
  readonly calibrated_probability: string | null;
  readonly effective_threshold: string;
  readonly extracted_value: AssertionExtractedValue | null;
  readonly evidence: readonly AssertionEvidence[];
  readonly features: Record<string, unknown> | null;
}

export interface AssertionReviewItem {
  readonly evaluation_id: string;
  readonly definition_id: string;
  readonly work_item_id: string;
  readonly verification_id: string | null;
  /** What the administrator wrote, verbatim. Never the compiled form. */
  readonly sentence: string;
  readonly understood_as: string;
  readonly family: AssertionFamily;
  readonly verdict: AssertionVerdict;
  readonly extracted_value: AssertionExtractedValue | null;
  readonly calibrated_probability: string | null;
  readonly raw_score: string;
  readonly evidence: readonly AssertionEvidence[];
  readonly document_name: string | null;
  readonly created_at: string | null;
}

export interface RetrievalPhrase {
  readonly id: string;
  readonly family: AssertionFamily;
  readonly phrase: string;
  readonly source: "SEED" | "REVIEWER";
  readonly hits: number;
  readonly created_at: string | null;
}

/** Human labels for a family. Used in the review queue and the builder. */
export const FAMILY_LABELS: Record<AssertionFamily, string> = {
  duration_bound: "Time limit",
  notice_period: "Notice period",
  money_multiple_bound: "Cap as a multiple",
  money_bound: "Amount or rate",
  enumerated: "One of a list",
  presence: "Clause must be present",
  absence: "Clause must be absent",
  llm: "Checked by the AI model",
};
