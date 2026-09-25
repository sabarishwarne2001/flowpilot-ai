/**
 * ARCH46-S2:types — Obligations & Temporal Intelligence. Mirrors
 * backend/app/schemas/obligations.py field for field (verify_arch46 W3 compares
 * them) and the vocabulary in backend/app/services/obligations/vocabulary.py.
 */

import { formatCalendarDate } from "@/utils/displayTime";

export type ObligationKind = "RENEWAL" | "NOTICE" | "PAYMENT" | "DELIVERY" | "EXPIRY" | "REPORTING" | "OTHER";
export type ObligationState = "OPEN" | "DUE_SOON" | "OVERDUE" | "DONE" | "WAIVED";
export type ObligationReview = "AUTO" | "PENDING" | "CONFIRMED" | "REJECTED";
export type ObligationOrigin = "EXTRACTED" | "MANUAL";
export type RuleKind = "FIXED" | "OFFSET" | "SERIES" | "NONE";
export type PeriodUnit = "DAY" | "BUSINESS_DAY" | "WEEK" | "MONTH" | "YEAR";
export type RollConvention = "NONE" | "FOLLOWING" | "PRECEDING" | "MODIFIED_FOLLOWING";
export type ObligationEventKind =
  | "CREATED" | "UPDATED" | "DUE_SOON" | "OVERDUE" | "REOPENED" | "DONE" | "WAIVED" | "ADVANCED" | "RESCHEDULED"
  | "CONFIRMED" | "REJECTED" | "SUPERSEDED";
export type FeedScope = "ALL" | "MINE";
export type CalendarSource = "TEMPLATE" | "MANUAL" | "ICS";
export type ObligationVerdict = "CONFIRM" | "REJECT";

export const KINDS: readonly ObligationKind[] = ["RENEWAL", "NOTICE", "PAYMENT", "DELIVERY", "EXPIRY", "REPORTING", "OTHER"];
export const STATES: readonly ObligationState[] = ["OPEN", "DUE_SOON", "OVERDUE", "DONE", "WAIVED"];
export const UNITS: readonly PeriodUnit[] = ["DAY", "BUSINESS_DAY", "WEEK", "MONTH", "YEAR"];
export const ROLLS: readonly RollConvention[] = ["NONE", "FOLLOWING", "PRECEDING", "MODIFIED_FOLLOWING"];

export const KIND_LABELS: Readonly<Record<ObligationKind, string>> = {
  RENEWAL: "Renewal",
  NOTICE: "Notice deadline",
  PAYMENT: "Payment",
  DELIVERY: "Delivery",
  EXPIRY: "Expiry",
  REPORTING: "Report",
  OTHER: "Other",
};

export const STATE_LABELS: Readonly<Record<ObligationState, string>> = {
  OPEN: "Open",
  DUE_SOON: "Due soon",
  OVERDUE: "Overdue",
  DONE: "Done",
  WAIVED: "Waived",
};

export const STATE_TONE: Readonly<Record<ObligationState, string>> = {
  OPEN: "bg-muted text-muted-foreground",
  DUE_SOON: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  OVERDUE: "bg-destructive/15 text-destructive",
  DONE: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  WAIVED: "bg-muted text-muted-foreground line-through",
};

export const REVIEW_LABELS: Readonly<Record<ObligationReview, string>> = {
  AUTO: "Read from the document",
  PENDING: "Needs confirming",
  CONFIRMED: "Confirmed",
  REJECTED: "Rejected",
};

export const UNIT_LABELS: Readonly<Record<PeriodUnit, string>> = {
  DAY: "days",
  BUSINESS_DAY: "business days",
  WEEK: "weeks",
  MONTH: "months",
  YEAR: "years",
};

export const ROLL_LABELS: Readonly<Record<RollConvention, string>> = {
  NONE: "Keep the date",
  FOLLOWING: "Next business day",
  PRECEDING: "Previous business day",
  MODIFIED_FOLLOWING: "Next business day, unless that is next month",
};

export const WEEKDAY_LABELS: readonly string[] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export interface PeriodSpec {
  readonly n: number;
  readonly unit: string;
}

export interface RuleSpec {
  readonly kind: string;
  readonly date?: string | null;
  readonly anchor_obligation_id?: string | null;
  readonly anchor_date?: string | null;
  readonly sign?: number | null;
  readonly period?: PeriodSpec | null;
  readonly shift_days?: number | null;
  readonly rrule?: string | null;
  readonly start?: string | null;
  readonly roll?: string | null;
}

export interface ObligationCreate {
  readonly kind: string;
  readonly title: string;
  readonly description?: string | null;
  readonly rule: RuleSpec;
  readonly owner_user_id?: string | null;
  readonly entity_id?: string | null;
  readonly work_item_id?: string | null;
  readonly calendar_id?: string | null;
  readonly lead_days?: number | null;
  readonly amount?: number | null;
  readonly currency?: string | null;
  readonly counterparty_name?: string | null;
}

export interface ObligationUpdate {
  readonly title?: string | null;
  readonly description?: string | null;
  readonly owner_user_id?: string | null;
  readonly entity_id?: string | null;
  readonly calendar_id?: string | null;
  readonly lead_days?: number | null;
  readonly kind?: string | null;
  readonly amount?: number | null;
  readonly currency?: string | null;
  readonly counterparty_name?: string | null;
  readonly due_date?: string | null;
  readonly rule?: RuleSpec | null;
  readonly business_day_rule?: string | null;
}

export interface ObligationBrief {
  readonly id: string;
  readonly kind: ObligationKind;
  readonly title: string;
  readonly due_date?: string | null;
  readonly state: ObligationState;
}

export interface ObligationRow {
  readonly id: string;
  readonly kind: ObligationKind;
  readonly title: string;
  readonly state: ObligationState;
  readonly review: ObligationReview;
  readonly origin: ObligationOrigin;
  readonly due_date?: string | null;
  readonly days_until?: number | null;
  readonly lead_days: number;
  readonly recurrence?: string | null;
  readonly recurrence_text?: string | null;
  readonly occurrence: number;
  readonly completed_occurrences: number;
  readonly business_day_rule: RollConvention;
  readonly calendar_id?: string | null;
  readonly anchor_obligation_id?: string | null;
  readonly owner_user_id?: string | null;
  readonly owner_email?: string | null;
  readonly entity_id?: string | null;
  readonly entity_root_id?: string | null;
  readonly entity_name?: string | null;
  readonly counterparty_name?: string | null;
  readonly work_item_id?: string | null;
  readonly work_item_filename?: string | null;
  readonly amount?: string | null;
  readonly currency?: string | null;
  readonly confidence: number;
  readonly reasons: readonly string[];
  readonly superseded: boolean;
  readonly created_at: string;
  readonly updated_at: string;
  readonly state_changed_at: string;
}

export interface ObligationList {
  readonly items: readonly ObligationRow[];
  readonly total: number;
  readonly counts_by_state: Readonly<Record<string, number>>;
  readonly pending_review: number;
  readonly today: string;
  readonly timezone: string;
}

export interface ObligationEventRow {
  readonly id: string;
  readonly kind: ObligationEventKind;
  readonly from_state?: ObligationState | null;
  readonly to_state?: ObligationState | null;
  readonly due_date?: string | null;
  readonly occurrence?: number | null;
  readonly emitted: boolean;
  readonly actor_user_id?: string | null;
  readonly detail: Readonly<Record<string, unknown>>;
  readonly created_at: string;
}

export interface OccurrenceRow {
  readonly occurrence: number;
  readonly due_date: string;
}

export interface EvidenceSpan {
  readonly page: number;
  readonly text?: string;
  readonly work_item_id?: string;
  readonly bbox?: { readonly x0: number; readonly y0: number; readonly x1: number; readonly y1: number };
}

export interface ObligationDetail {
  readonly obligation: ObligationRow;
  readonly description?: string | null;
  readonly due_rule: Readonly<Record<string, unknown>>;
  readonly derivation: readonly string[];
  readonly evidence: readonly EvidenceSpan[];
  readonly quote?: string | null;
  readonly clause_number?: string | null;
  readonly detail: Readonly<Record<string, unknown>>;
  readonly events: readonly ObligationEventRow[];
  readonly upcoming: readonly OccurrenceRow[];
  readonly anchor?: ObligationBrief | null;
  readonly dependents: readonly ObligationBrief[];
  readonly done_at?: string | null;
  readonly done_by_user_id?: string | null;
  readonly waived_at?: string | null;
  readonly waiver_reason?: string | null;
  readonly reviewed_at?: string | null;
  readonly reviewed_by_user_id?: string | null;
  readonly engine_version?: string | null;
  readonly calendar_name?: string | null;
  readonly today: string;
  readonly timezone: string;
}

export interface CalendarOccurrence {
  readonly obligation_id: string;
  readonly occurrence: number;
  readonly due_date: string;
  readonly kind: ObligationKind;
  readonly title: string;
  readonly state: ObligationState;
  readonly review: ObligationReview;
  readonly owner_user_id?: string | null;
  readonly projected: boolean;
}

export interface CalendarView {
  readonly from_date: string;
  readonly to_date: string;
  readonly today: string;
  readonly timezone: string;
  readonly items: readonly CalendarOccurrence[];
}

export interface CompleteRequest {
  readonly note?: string | null;
}

export interface WaiveRequest {
  readonly reason: string;
}

export interface ReviewRequest {
  readonly verdict: string;
}

export interface ExtractResult {
  readonly extracted: boolean;
  readonly created: number;
  readonly kept: number;
  readonly revived: number;
  readonly superseded: number;
  readonly pending: number;
  readonly engine_version: string;
}

export interface DocumentObligations {
  readonly work_item_id: string;
  readonly original_filename: string;
  readonly items: readonly ObligationRow[];
  readonly today: string;
}

export interface EntityObligations {
  readonly entity_id: string;
  readonly root_id: string;
  readonly items: readonly ObligationRow[];
  readonly today: string;
}

export interface HolidayRow {
  readonly date: string;
  readonly name: string;
}

export interface TemplateRow {
  readonly code: string;
  readonly name: string;
  readonly region: string;
  readonly weekend_days: readonly number[];
  readonly description: string;
}

export interface HolidayCalendarRow {
  readonly id: string;
  readonly name: string;
  readonly source: CalendarSource;
  readonly template_code?: string | null;
  readonly region?: string | null;
  readonly weekend_days: readonly number[];
  readonly holidays: readonly HolidayRow[];
  readonly is_default: boolean;
  readonly revision: number;
  readonly obligations: number;
  readonly updated_at: string;
}

export interface HolidayCalendarList {
  readonly items: readonly HolidayCalendarRow[];
  readonly templates: readonly TemplateRow[];
}

export interface HolidayCalendarCreate {
  readonly name: string;
  readonly template?: string | null;
  readonly years?: readonly number[];
  readonly holidays?: readonly HolidayRow[];
  readonly ics?: string | null;
  readonly weekend_days?: readonly number[] | null;
  readonly is_default?: boolean;
}

export interface HolidayCalendarUpdate {
  readonly name?: string | null;
  readonly holidays?: readonly HolidayRow[] | null;
  readonly weekend_days?: readonly number[] | null;
  readonly is_default?: boolean | null;
}

export interface CalendarUpdateResult {
  readonly calendar: HolidayCalendarRow;
  readonly rescheduled: number;
}

export interface FeedRow {
  readonly id: string;
  readonly label: string;
  readonly scope: FeedScope;
  readonly include_closed: boolean;
  readonly user_id: string;
  readonly mine: boolean;
  readonly created_at: string;
  readonly expires_at?: string | null;
  readonly last_used_at?: string | null;
  readonly use_count: number;
  readonly revoked_at?: string | null;
}

export interface FeedList {
  readonly items: readonly FeedRow[];
}

export interface FeedCreate {
  readonly label?: string | null;
  readonly scope: string;
  readonly include_closed?: boolean;
  readonly expires_in_days?: number | null;
}

export interface FeedIssued {
  readonly feed: FeedRow;
  readonly token: string;
  readonly path: string;
}

export interface DateCalculationRequest {
  readonly rule: RuleSpec;
  readonly anchor_due?: string | null;
  readonly calendar_id?: string | null;
  readonly occurrences?: number;
}

export interface DateCalculation {
  readonly due_date?: string | null;
  readonly steps: readonly string[];
  readonly upcoming: readonly OccurrenceRow[];
}

/** "in 3 days", "today", "2 days overdue". */
export const dueLabel = (row: Pick<ObligationRow, "days_until" | "state">): string => {
  const d = row.days_until;
  if (d === null || d === undefined) {
    return "no date yet";
  }
  if (row.state === "DONE" || row.state === "WAIVED") {
    return "";
  }
  if (d === 0) {
    return "today";
  }
  if (d > 0) {
    return d === 1 ? "tomorrow" : `in ${d} days`;
  }
  return -d === 1 ? "1 day overdue" : `${-d} days overdue`;
};

/** A YYYY-MM-DD date as a readable local date (it IS a local date: no time zone shift; the profile's language). */
export const formatDay = (iso?: string | null): string => formatCalendarDate(iso);
