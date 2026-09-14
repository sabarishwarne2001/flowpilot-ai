/**
 * ARCH-31 Step 4 — the procurement matching wire contract.
 *
 * WHY OUTCOMES ARE A UNION AND NOT A STRING
 * =========================================
 * The grid renders a colour and a sentence per outcome. A `string` here means
 * a backend that adds a seventh outcome renders as an uncoloured blank cell
 * and nobody finds out until a reviewer asks what the empty column means.
 * With the union, `outcomeLabel`'s exhaustive switch stops compiling.
 */

export const CASE_STATUSES = [
  "OPEN",
  "MATCHED",
  "NEEDS_REVIEW",
  "APPROVED",
  "DISPUTED",
  "SUPERSEDED",
] as const;
export type CaseStatus = (typeof CASE_STATUSES)[number];

export const LINE_OUTCOMES = [
  "MATCHED",
  "PRICE_VARIANCE",
  "QUANTITY_VARIANCE",
  "NOT_RECEIVED",
  "NOT_ORDERED",
  "NOT_INVOICED",
] as const;
export type LineOutcome = (typeof LINE_OUTCOMES)[number];

/** Everything that is not a clean match. Mirrors RED_OUTCOMES on the server. */
export const RED_OUTCOMES: ReadonlySet<LineOutcome> = new Set(
  LINE_OUTCOMES.filter((outcome) => outcome !== "MATCHED"),
);

export interface EvidencePointer {
  readonly work_item_id: string;
  readonly line_index: number;
  readonly page?: number | null;
  readonly char_start?: number | null;
  readonly char_end?: number | null;
  readonly bbox?: Record<string, number> | null;
}

export interface CaseFinding {
  readonly code: string;
  readonly detail?: string;
  readonly side?: string;
  readonly line_index?: number;
  readonly [key: string]: unknown;
}

export interface CaseLine {
  readonly id: string;
  readonly line_number: number;
  readonly outcome: LineOutcome;
  readonly description: string | null;
  readonly sku: string | null;
  readonly po_line_index: number | null;
  readonly po_quantity: string | null;
  readonly po_unit_price_micros: number | null;
  readonly po_amount_micros: number | null;
  readonly receipt_line_index: number | null;
  readonly receipt_quantity: string | null;
  readonly invoice_line_index: number | null;
  readonly invoice_quantity: string | null;
  readonly invoice_unit_price_micros: number | null;
  readonly invoice_amount_micros: number | null;
  readonly pair_cost: number | null;
  readonly price_delta_micros: number | null;
  readonly quantity_delta: string | null;
  readonly findings: readonly CaseFinding[];
  readonly evidence: Partial<Record<"po" | "receipt" | "invoice", EvidencePointer>>;
}

export interface CaseSummary {
  readonly id: string;
  readonly status: CaseStatus;
  readonly vendor_key: string | null;
  readonly po_work_item_id: string | null;
  readonly receipt_work_item_id: string | null;
  readonly invoice_work_item_id: string | null;
  readonly line_count: number;
  readonly exception_count: number;
  readonly variance_micros: number;
  readonly policy_version: string;
  readonly created_at: string;
  readonly updated_at: string;
  readonly resolved_at: string | null;
}

export interface CaseDetail extends CaseSummary {
  readonly input_digest: string;
  readonly header_findings: readonly CaseFinding[];
  readonly resolution_reason: string | null;
  readonly resolved_by_user_id: string | null;
  readonly lines: readonly CaseLine[];
}

export interface TolerancePolicy {
  readonly id: string;
  readonly version: number;
  readonly status: "DRAFT" | "PUBLISHED";
  readonly price_tolerance_micros: number;
  readonly price_tolerance_bps: number;
  readonly quantity_tolerance: string;
  readonly max_pair_cost: number;
  readonly candidate_window_days: number;
  readonly published_at: string | null;
  readonly created_at: string;
}

export interface ImpactPreview {
  readonly window_days: number;
  readonly cases_considered: number;
  readonly exceptions_today: number;
  readonly exceptions_under_draft: number;
  readonly lines_that_would_clear: number;
  readonly lines_that_would_flag: number;
  readonly caveat: string;
}

/**
 * Result-cell prose. Text AND colour, never colour alone: a reviewer with a
 * colour-vision deficiency reading a grid of amber and red cells has no
 * information at all, and this is a screen people approve payments from.
 */
export const outcomeLabel = (line: CaseLine): string => {
  switch (line.outcome) {
    case "MATCHED":
      return "Match";
    case "PRICE_VARIANCE": {
      const delta = line.price_delta_micros ?? 0;
      const base = line.po_unit_price_micros ?? 0;
      if (base === 0) {
        return delta > 0 ? "Price higher" : "Price lower";
      }
      const pct = (delta / base) * 100;
      return `${pct > 0 ? "+" : ""}${pct.toFixed(1)}% price`;
    }
    case "QUANTITY_VARIANCE": {
      const delta = line.quantity_delta ?? "0";
      const numeric = Number(delta);
      return `${numeric > 0 ? "+" : ""}${delta} qty`;
    }
    case "NOT_RECEIVED":
      return "Not received";
    case "NOT_ORDERED":
      return "Not ordered";
    case "NOT_INVOICED":
      return "Not invoiced";
    default: {
      // Exhaustiveness: adding an outcome to the union without a label here
      // is a compile error rather than a blank cell in production.
      const exhaustive: never = line.outcome;
      return exhaustive;
    }
  }
};

export const outcomeTone = (outcome: LineOutcome): string => {
  switch (outcome) {
    case "MATCHED":
      return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/30";
    case "PRICE_VARIANCE":
    case "QUANTITY_VARIANCE":
      return "bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/30";
    default:
      return "bg-destructive/10 text-destructive border-destructive/30";
  }
};

export const statusTone = (status: CaseStatus): string => {
  switch (status) {
    case "MATCHED":
    case "APPROVED":
      return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
    case "NEEDS_REVIEW":
      return "bg-amber-500/10 text-amber-700 dark:text-amber-300";
    case "DISPUTED":
      return "bg-destructive/10 text-destructive";
    default:
      return "bg-muted text-muted-foreground";
  }
};
