import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, CircleAlert, FileCheck2, Loader2, Scale, SlidersHorizontal } from "lucide-react";

import CapabilityLockCard from "@/components/procurement/CapabilityLockCard";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { listCases } from "@/services/api/procurement";
import { procurementKeys } from "@/services/api/queryKeys";
import {
  BUTTON_SECONDARY,
  HINT,
  PAGE_TITLE,
  SCROLL_X,
  SURFACE,
  TABLE_HEAD,
  TABLE_ROW,
} from "@/components/ui/primitives";
import type { CaseStatus, CaseSummary } from "@/types/procurement";
import { statusTone } from "@/types/procurement";
import { formatTimestamp } from "@/utils/displayTime";
import { formatMoneyMicros, vendorLabel } from "@/utils/formatters";
import { ErrorState } from "@/components/common/ErrorState";
import { errorMessage } from "@/services/api/errors";

const RECONCILIATION_CAPABILITY = "capability.reconciliation";

const FILTERS: readonly { label: string; statuses?: readonly CaseStatus[] }[] = [
  { label: "All open" },
  { label: "Needs review", statuses: ["NEEDS_REVIEW"] },
  { label: "Matched", statuses: ["MATCHED"] },
  { label: "Approved", statuses: ["APPROVED"] },
  { label: "Disputed", statuses: ["DISPUTED"] },
];

const STATUS_LABEL: Readonly<Record<CaseStatus, string>> = {
  OPEN: "Open",
  MATCHED: "Matched",
  NEEDS_REVIEW: "Needs review",
  APPROVED: "Approved",
  DISPUTED: "Disputed",
  SUPERSEDED: "Superseded",
};

/**
 * The three-way matching queue.
 *
 * Phase 2: each row names the invoice, the PO and the receipt by the numbers
 * printed on them and the vendor by the name the invoice prints (F-174: the
 * normalised matching key is not a label), and every amount is formatted in
 * the case's own currency (F-172: it was a constant, so every variance read
 * as rupees). The strip above counts what is open, from the same rows.
 */
export const CaseQueue: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const organizationId = workspace?.organizationId ?? "";
  const navigate = useNavigate();

  const capability = useCapabilityAccess(organizationId, RECONCILIATION_CAPABILITY);
  const [filterIndex, setFilterIndex] = useState(0);
  const [onlyExceptions, setOnlyExceptions] = useState(false);

  const fallbackFilter = FILTERS[0]!;
  const active = FILTERS[filterIndex] ?? fallbackFilter;

  const query = useQuery({
    queryKey: procurementKeys.cases(workspaceId, active.label, onlyExceptions),
    queryFn: () => {
      const filters: Record<string, any> = { limit: 100 };
      if (active.statuses) {
        filters.status = active.statuses;
      }
      if (onlyExceptions) {
        filters.hasExceptions = true;
      }
      return listCases(workspaceId, filters as any);
    },
    enabled: Boolean(workspaceId) && capability.granted,
    staleTime: 15_000,
  });

  // The strip always describes everything open, whichever filter the table shows.
  const overview = useQuery({
    queryKey: procurementKeys.cases(workspaceId, "All open", false),
    queryFn: () => listCases(workspaceId, { limit: 100 } as any),
    enabled: Boolean(workspaceId) && capability.granted,
    staleTime: 15_000,
  });

  const cases = useMemo<CaseSummary[]>(() => query.data ?? [], [query.data]);

  const stats = useMemo(() => {
    const rows = overview.data ?? [];
    const count = (status: CaseStatus) => rows.filter((row) => row.status === status).length;
    const currencies = new Set(rows.map((row) => row.currency ?? ""));
    const single = currencies.size === 1 ? [...currencies][0] || null : null;
    const variance = rows
      .filter((row) => row.status === "NEEDS_REVIEW" || row.status === "MATCHED" || row.status === "OPEN")
      .reduce((sum, row) => sum + Math.abs(row.variance_micros), 0);
    const nothingCompared = rows.filter((row) => row.line_count === 0 && row.status === "NEEDS_REVIEW").length;
    return {
      total: rows.length,
      review: count("NEEDS_REVIEW"),
      matched: count("MATCHED"),
      approved: count("APPROVED"),
      disputed: count("DISPUTED"),
      variance,
      currency: single,
      mixedCurrencies: currencies.size > 1,
      nothingCompared,
    };
  }, [overview.data]);

  if (capability.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading…
      </div>
    );
  }

  if (!capability.granted) {
    return (
      <div className="p-6">
        <CapabilityLockCard canChangePlan={workspace?.role === "ADMIN"} />
      </div>
    );
  }

  return (
    <div className="space-y-5 p-6">
      {/* ARCH36-S1:policies-link — the tolerance editor had a route and no way in. */}
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className={PAGE_TITLE}>Invoice matching</h1>
          <p className={`${HINT} mt-1 max-w-2xl`}>
            Every invoice checked line by line against its purchase order and goods receipt. Anything that
            does not match, or could not be compared, waits here for a person.
          </p>
        </div>
        <Link to="./policies" className={`${BUTTON_SECONDARY} inline-flex items-center gap-1.5`}>
          <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden />
          Tolerance policies
        </Link>
      </header>

      <section aria-label="Matching at a glance" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile
          icon={<CircleAlert className="h-4 w-4" aria-hidden />}
          label="Needs review"
          value={stats.review}
          tone={stats.review > 0 ? "warn" : "muted"}
          hint={stats.nothingCompared > 0 ? `${stats.nothingCompared} with no line compared` : "Exceptions to decide"}
        />
        <StatTile
          icon={<FileCheck2 className="h-4 w-4" aria-hidden />}
          label="Matched"
          value={stats.matched}
          tone="good"
          hint="Ready to approve"
        />
        <StatTile
          icon={<Scale className="h-4 w-4" aria-hidden />}
          label="Open variance"
          value={stats.mixedCurrencies ? "Mixed" : formatMoneyMicros(stats.variance, stats.currency)}
          tone={stats.variance > 0 ? "warn" : "muted"}
          hint={stats.mixedCurrencies ? "Cases in several currencies" : "Absolute, across open cases"}
        />
        <StatTile
          icon={<FileCheck2 className="h-4 w-4" aria-hidden />}
          label="Resolved"
          value={stats.approved + stats.disputed}
          tone="muted"
          hint={`${stats.approved} approved · ${stats.disputed} disputed`}
        />
      </section>

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((filter, index) => (
          <button
            key={filter.label}
            type="button"
            onClick={() => setFilterIndex(index)}
            aria-pressed={index === filterIndex}
            className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
              index === filterIndex
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:bg-muted"
            }`}
          >
            {filter.label}
          </button>
        ))}
        <label className="ml-2 flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={onlyExceptions}
            onChange={(event) => setOnlyExceptions(event.target.checked)}
          />
          Only cases with exceptions
        </label>
      </div>

      {query.isError ? (
        <ErrorState
          title="Cases could not be loaded"
          description={errorMessage(query.error, "The server did not return cases. Check your connection and try again.")}
          onRetry={() => void query.refetch()}
        />
      ) : query.isLoading ? (
        <p className={HINT}>Loading cases…</p>
      ) : cases.length === 0 ? (
        <section className={`${SURFACE} p-6`}>
          <p className="text-sm text-muted-foreground">
            No cases here yet. Matching runs when an invoice and its purchase
            order or goods receipt have both been extracted.
          </p>
        </section>
      ) : (
        <div className={`${SURFACE} ${SCROLL_X}`}>
          <table className="w-full min-w-[860px] text-sm">
            <thead className={TABLE_HEAD}>
              <tr>
                <th className="px-4 py-2.5 text-left">Status</th>
                <th className="px-4 py-2.5 text-left">Invoice</th>
                <th className="px-4 py-2.5 text-left">Matched against</th>
                <th className="px-4 py-2.5 text-right">Lines</th>
                <th className="px-4 py-2.5 text-right">Exceptions</th>
                <th className="px-4 py-2.5 text-right">Variance</th>
                <th className="px-4 py-2.5 text-left">Scored</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {cases.map((row) => {
                const nothingCompared = row.line_count === 0;
                return (
                  <tr
                    key={row.id}
                    className={`${TABLE_ROW} cursor-pointer`}
                    onClick={(event) => {
                      if ((event.target as HTMLElement).closest("a")) {
                        return;
                      }
                      navigate(`./${row.id}`);
                    }}
                  >
                    <td className="px-4 py-3 align-top">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${statusTone(row.status)}`}
                      >
                        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
                        {STATUS_LABEL[row.status] ?? row.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 align-top">
                      <div className="font-medium text-foreground">
                        {row.invoice_number ?? row.invoice_filename ?? "Invoice"}
                      </div>
                      <div className="text-xs text-muted-foreground">{vendorLabel(row.vendor_name, row.vendor_key)}</div>
                    </td>
                    <td className="px-4 py-3 align-top text-xs">
                      <div className="flex flex-col gap-0.5">
                        {row.po_work_item_id ? (
                          <span>
                            <span className="text-muted-foreground">PO </span>
                            <span className="font-mono">{row.po_number ?? row.po_filename ?? "—"}</span>
                          </span>
                        ) : (
                          <span className="text-muted-foreground">No purchase order</span>
                        )}
                        {row.receipt_work_item_id ? (
                          <span>
                            <span className="text-muted-foreground">Receipt </span>
                            <span className="font-mono">{row.receipt_number ?? row.receipt_filename ?? "—"}</span>
                          </span>
                        ) : (
                          <span className="text-muted-foreground">No goods receipt</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right align-top tabular-nums">
                      {nothingCompared ? (
                        <span className="text-xs text-amber-700 dark:text-amber-300" title="No line item could be read on any document">
                          none read
                        </span>
                      ) : (
                        row.line_count
                      )}
                    </td>
                    <td className="px-4 py-3 text-right align-top tabular-nums">
                      {row.exception_count > 0 ? (
                        <span className="font-medium text-destructive">{row.exception_count}</span>
                      ) : (
                        <span className="text-muted-foreground">0</span>
                      )}
                    </td>
                    <td
                      className={`px-4 py-3 text-right align-top tabular-nums ${
                        row.variance_micros > 0 ? "text-destructive" : row.variance_micros < 0 ? "text-emerald-700 dark:text-emerald-300" : ""
                      }`}
                    >
                      {formatMoneyMicros(row.variance_micros, row.currency, { signed: true })}
                    </td>
                    {/* Every timestamp goes through displayTime, never toLocaleString. */}
                    <td className="px-4 py-3 align-top text-xs text-muted-foreground">
                      {formatTimestamp(row.created_at)}
                    </td>
                    <td className="px-4 py-3 text-right align-top">
                      <Link
                        to={`./${row.id}`}
                        className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                      >
                        Review
                        <ArrowRight className="h-3 w-3" aria-hidden />
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

const TONES = {
  good: "text-emerald-700 dark:text-emerald-300",
  warn: "text-amber-700 dark:text-amber-300",
  muted: "text-foreground",
} as const;

const StatTile: React.FC<{
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  hint: string;
  tone: keyof typeof TONES;
}> = ({ icon, label, value, hint, tone }) => (
  <div className={`${SURFACE} p-4`}>
    <div className="flex items-center justify-between text-muted-foreground">
      <span className="text-[11px] font-semibold uppercase tracking-[0.06em]">{label}</span>
      {icon}
    </div>
    <div className={`mt-2 text-2xl font-semibold tabular-nums tracking-tight ${TONES[tone]}`}>{value}</div>
    <div className="mt-1 truncate text-xs text-muted-foreground">{hint}</div>
  </div>
);

export default CaseQueue;
