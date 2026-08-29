export type SpendLimitPeriod = "DAY" | "MONTH";

/** Mirrors is_limit_key(): TOTAL_COST_KEY or any billable usage event type. */
export const SPEND_LIMIT_KEYS = [
  "*",
  "ocr.page",
  "embedding.token",
  "llm.input_token",
  "llm.output_token",
  "storage.gb_month",
  "document.processed",
] as const;

export type SpendLimitKey = (typeof SPEND_LIMIT_KEYS)[number];

export interface SpendLimitUpdateRequest {
  readonly limit_key: string;
  readonly period: SpendLimitPeriod;
  readonly max_quantity?: string | number | null;
  readonly max_cost_micros?: number | null;
  readonly hard_stop: boolean;
  readonly note?: string | null;
}

export interface SpendLimit {
  readonly id: string;
  readonly organization_id: string;
  readonly limit_key: string;
  readonly period: SpendLimitPeriod;
  readonly max_quantity: string | null;
  readonly max_cost_micros: number | null;
  readonly hard_stop: boolean;
  readonly is_active: boolean;
  readonly note: string | null;
}
