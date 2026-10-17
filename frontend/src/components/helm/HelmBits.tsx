/** Helm's small shared pieces: state words, Guard badges, money figures, the visibility meter. */
import React from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, ShieldAlert, ShieldCheck, ShieldQuestion, XCircle } from "lucide-react";

import { formatHelmMoney } from "@/components/helm/money";
import { formatNumber } from "@/utils/displayTime";
import type { ActionKind, DriftState, HelmAction, PlanSummary } from "@/types/helm";

export const STATE_WORDS: Record<DriftState, string> = {
  holding: "Holding",
  drifting: "Drifting",
  breached: "Breached",
  unknown: "Not enough data",
};

const STATE_ICONS: Record<DriftState, React.ComponentType<{ className?: string; "aria-hidden"?: "true" }>> = {
  holding: CheckCircle2,
  drifting: AlertTriangle,
  breached: XCircle,
  unknown: CircleDashed,
};

/** A mandate's state as a word and an icon, never colour alone. */
export const StateBadge: React.FC<{ readonly state: DriftState }> = ({ state }) => {
  const Icon = STATE_ICONS[state] ?? CircleDashed;
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold helm-state-${state}`}>
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {STATE_WORDS[state] ?? state}
    </span>
  );
};

/** The plan as a diff (brief §10.1): pay, hold, chase, request, decide. */
export const ACTION_VERBS: Record<ActionKind, string> = {
  schedule_payment: "Pay",
  approve_payment: "Pay",
  hold_payment: "Hold",
  release_hold: "Release",
  send_reminder: "Chase",
  request_document: "Request",
  decide_renewal: "Decide",
  create_task: "Decide",
  draft_dispute: "Dispute",
  draft_reconciliation: "Reconcile",
  mark_paid: "Record",
  notify: "Notify",
  webhook: "Notify",
};

export const ACTION_STATE_WORDS: Record<string, string> = {
  PROPOSED: "Proposed",
  APPROVED: "Approved",
  EXECUTING: "Running",
  EXECUTED: "Done",
  REJECTED: "Rejected",
  BLOCKED: "Blocked",
  UNDONE: "Undone",
  SUPERSEDED: "Replaced",
  FAILED: "Failed",
};

/** Who approved, as a role (never a person's name). */
export const ROLE_WORDS: Record<string, string> = { OWNER: "owner", ADMIN: "admin", BILLING: "billing" };

const ROLE_ORDER = ["OWNER", "ADMIN", "BILLING"] as const;
const ROLE_PLURALS: Record<string, string> = { OWNER: "owners", ADMIN: "admins", BILLING: "the billing role" };

/** ["ADMIN", "OWNER"] -> "owners or admins"; none -> everyone who may approve a payment. */
export const rolesWords = (roles: readonly string[] | null | undefined): string => {
  const names = ROLE_ORDER.filter((r) => (roles ?? []).includes(r)).map((r) => ROLE_PLURALS[r] ?? r);
  const last = names.pop();
  if (last === undefined) {
    return "owners, admins or the billing role";
  }
  return names.length === 0 ? last : `${names.join(", ")} or ${last}`;
};

export const GuardBadge: React.FC<{ readonly action: Pick<HelmAction, "guard_passed" | "needs_approval" | "state"> }> = ({
  action,
}) => {
  if (!action.guard_passed || action.state === "BLOCKED") {
    return (
      <span className="helm-chip helm-state-breached" title="The Guard refused this action">
        <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
        Blocked by the Guard
      </span>
    );
  }
  if (action.needs_approval) {
    return (
      <span className="helm-chip helm-state-drifting">
        <ShieldQuestion className="h-3.5 w-3.5" aria-hidden="true" />
        Guard passed · needs approval
      </span>
    );
  }
  return (
    <span className="helm-chip helm-state-holding">
      <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
      Guard passed
    </span>
  );
};

export const Money: React.FC<{
  readonly micros: number | null | undefined;
  readonly currency: string | null | undefined;
  readonly className?: string;
}> = ({ micros, currency, className = "" }) => (
  <span className={`helm-figure ${className}`}>{formatHelmMoney(micros ?? null, currency ?? null)}</span>
);

/** Totals in several currencies, each in its own (Helm never converts between currencies). */
export const MoneyByCurrency: React.FC<{ readonly totals: Record<string, number> | null | undefined; readonly empty?: string }> = ({
  totals,
  empty = "None yet",
}) => {
  const entries = Object.entries(totals ?? {}).filter(([, value]) => value !== 0);
  if (entries.length === 0) {
    return <span className="helm-muted">{empty}</span>;
  }
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-1">
      {entries.map(([currency, micros]) => (
        <Money key={currency} micros={micros} currency={currency} />
      ))}
    </span>
  );
};

export const percent = (share: number | null | undefined): string =>
  share === null || share === undefined ? "—" : formatNumber(share, { style: "percent", maximumFractionDigits: 0 });

/**
 * The visibility meter (brief §10.1): how much of the business Helm can see. Never presented as
 * more than it is: an empty part says so instead of showing 0% or 100%.
 */
export const VisibilityMeter: React.FC<{ readonly visibility: PlanSummary["visibility"] }> = ({ visibility }) => {
  const parts: { key: "payables" | "suppliers" | "contracts"; label: string; detail: (p: Record<string, number | null>) => string }[] = [
    { key: "payables", label: "Payables", detail: (p) => `${p.with_key_facts ?? 0} of ${p.seen ?? 0} with due date, amount and supplier` },
    { key: "suppliers", label: "Suppliers", detail: (p) => `${p.with_terms ?? 0} of ${p.seen ?? 0} with payment terms` },
    { key: "contracts", label: "Contracts", detail: (p) => `${p.dated ?? 0} of ${p.seen ?? 0} with a date Helm can watch` },
  ];
  const overall = visibility?.overall ?? null;
  return (
    <section aria-labelledby="helm-visibility" className="helm-panel p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="helm-visibility" className="helm-label">
          Visibility
        </h2>
        <span className="helm-figure text-lg font-semibold">{percent(overall)}</span>
      </div>
      <p className="helm-muted mt-1 text-xs">What Helm can see of the business. It plans only with what it sees.</p>
      <ul className="mt-3 space-y-2.5">
        {parts.map((part) => {
          const data = (visibility?.[part.key] ?? null) as Record<string, number | null> | null;
          const share = data?.share ?? null;
          return (
            <li key={part.key}>
              <div className="flex justify-between text-xs">
                <span>{part.label}</span>
                <span className="helm-figure">{share === null ? "nothing seen yet" : percent(share)}</span>
              </div>
              <div
                className="helm-meter-track mt-1 h-1.5 overflow-hidden rounded-full"
                role="meter"
                aria-label={`${part.label} visibility`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={share === null ? undefined : Math.round(share * 100)}
                aria-valuetext={share === null ? "Nothing seen yet" : `${percent(share)}: ${part.detail(data ?? {})}`}
              >
                <div className="helm-meter-fill h-full rounded-full" style={{ width: `${Math.round((share ?? 0) * 100)}%` }} />
              </div>
              {data ? <p className="helm-muted mt-0.5 text-[11px]">{part.detail(data)}</p> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
};

/** A thin instrument ring: a full circle in the state's colour, a partial one when a share is known. */
export const InstrumentRing: React.FC<{ readonly state: DriftState; readonly share?: number | null; readonly size?: number }> = ({
  state,
  share = null,
  size = 44,
}) => {
  const radius = size / 2 - 3;
  const circumference = 2 * Math.PI * radius;
  const filled = share === null ? 1 : Math.max(0, Math.min(1, share));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" className="shrink-0">
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" strokeWidth="2" className="helm-ring-track" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        strokeWidth="2.5"
        strokeLinecap="round"
        className={`helm-ring-${state} helm-motion`}
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - filled)}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
};
