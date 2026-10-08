/**
 * ARCH49-S2:process-common — pieces every Process Intelligence view shares: the
 * plan lock, the proposal status badge, money from the cost truth (USD micros;
 * UNKNOWN cost is reported, never shown as zero), durations and activity names.
 */
import React from "react";
import { Lock } from "lucide-react";

import { HINT, SECTION_TITLE, SURFACE, SURFACE_INSET } from "@/components/ui/primitives";
import { ApiError } from "@/services/api/errors";
import {
  CONFLICT_MESSAGES, STATUS_LABELS, STATUS_TONES, type CostSummary, type ProposalConflictCode, type ProposalStatus,
} from "@/types/process";
import { ViewPlansAction } from "@/components/billing/ViewPlansAction";

export const ProcessLocked: React.FC<{ readonly compact?: boolean }> = ({ compact = false }) =>
  compact ? (
    <section className={`${SURFACE_INSET} flex items-center gap-2 p-3 text-sm`} aria-label="Process intelligence">
      <Lock className="h-4 w-4" aria-hidden />
      <span>Process intelligence and the exception agent are included on the Enterprise plan.</span>
    </section>
  ) : (
    <section className={`${SURFACE} mx-auto mt-6 max-w-2xl space-y-3 p-6`} aria-labelledby="process-lock">
      <div className="flex items-center gap-2">
        <Lock className="h-4 w-4" aria-hidden />
        <h2 id="process-lock" className={SECTION_TITLE}>Process intelligence</h2>
      </div>
      <p className="text-sm text-muted-foreground">
        See how work actually flows — documents, cases, postings, review items and flow runs, event by event — find
        the variants that cost the most, replay your flows and case templates against what really happened, get warned
        before a service level is breached, and let an exception agent propose how to clear the review queue. Nothing
        it proposes is applied without a person, except the few decisions your own calibration bounds.
      </p>
      <p className={HINT}>It&apos;s included on the Enterprise plan.</p>
      <ViewPlansAction />
    </section>
  );

export const ProposalStatusBadge: React.FC<{ readonly status: ProposalStatus }> = ({ status }) => (
  <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_TONES[status]}`}>
    {STATUS_LABELS[status]}
  </span>
);

/** USD micros → "$0.0123". */
export const usd = (micros: number | null | undefined): string => {
  if (micros === null || micros === undefined) {
    return "—";
  }
  const dollars = micros / 1_000_000;
  const digits = Math.abs(dollars) >= 1 ? 2 : 4;
  return `$${dollars.toFixed(digits)}`;
};

const COUNT = new Intl.NumberFormat();

/** A count, grouped ("12,480"). */
export const num = (value: number): string => COUNT.format(value);

export const pct = (value: number | null | undefined, digits = 0): string =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

export const duration = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined) {
    return "—";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)}s`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m`;
  }
  if (seconds < 86_400) {
    return `${(seconds / 3600).toFixed(1)}h`;
  }
  return `${(seconds / 86_400).toFixed(1)}d`;
};

/** "posting.attempt.send.failed" → "posting › attempt › send › failed". */
const JOB_OUTCOME: Readonly<Record<string, string>> = {
  enqueued: "queued",
  started: "started",
  succeeded: "done",
  failed: "failed",
  retried: "retried",
  cancelled: "cancelled",
};

const sentence = (words: string): string => {
  const text = words.split("_").join(" ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
};

/**
 * An activity key in words: "job.document_extract.enqueued" reads "Document extract · queued",
 * "document.uploaded" reads "Document uploaded" (Phase 2; the raw key, "job › document extract ›
 * enqueued", was cut off in the discovery map).
 */
export const activityLabel = (activity: string): string => {
  const parts = activity.split(".");
  if (parts[0] === "job" && parts.length === 3) {
    return `${sentence(parts[1] ?? "")} · ${JOB_OUTCOME[parts[2] ?? ""] ?? sentence(parts[2] ?? "").toLowerCase()}`;
  }
  return sentence(parts.join(" "));
};

/** The cost line: the known figure, and how much of it is unknown (never summed as zero). */
export const CostCell: React.FC<{ readonly cost: CostSummary | null | undefined; readonly perObject?: boolean }> = ({ cost, perObject = true }) => {
  if (!cost || cost.objects === 0) {
    return <span className={HINT}>—</span>;
  }
  const figure = perObject ? cost.mean_reconciled_micros : cost.reconciled_micros;
  return (
    <span className="tabular-nums">
      {usd(figure)}
      {cost.unknown_events > 0 ? (
        <span className="ml-1 text-[11px] text-amber-700 dark:text-amber-400" title="Some metered events have no settled cost basis yet; they are counted, never priced at zero.">
          +{cost.unknown_events} unknown
        </span>
      ) : null}
    </span>
  );
};

export const canContribute = (role?: string | null): boolean => role === "ADMIN" || role === "OWNER" || role === "CONTRIBUTOR";
export const canAdminister = (role?: string | null): boolean => role === "ADMIN" || role === "OWNER";

export const conflictCode = (error: unknown): ProposalConflictCode | undefined =>
  error instanceof ApiError && error.code && error.code in CONFLICT_MESSAGES ? (error.code as ProposalConflictCode) : undefined;

export const actionError = (error: unknown, fallback: string): string => {
  const code = conflictCode(error);
  if (code) {
    return CONFLICT_MESSAGES[code];
  }
  return error instanceof ApiError ? error.message : fallback;
};
