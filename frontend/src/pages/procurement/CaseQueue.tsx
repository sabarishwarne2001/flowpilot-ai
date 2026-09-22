import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Loader2 } from "lucide-react";

import CapabilityLockCard from "@/components/procurement/CapabilityLockCard";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { listCases } from "@/services/api/procurement";
import { procurementKeys } from "@/services/api/queryKeys";
import {
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

/**
 * Amounts are micros. Formatted with the workspace currency rather than the
 * browser locale's default: a tenant in Chennai billing in EUR must not see
 * their variances rendered as rupees because that is what the browser guessed.
 */
const formatMicros = (micros: number, currency: string): string =>
  new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(micros / 1_000_000);

export const CaseQueue: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const organizationId = workspace?.organizationId ?? "";
  const currency = "INR";

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

  const cases = useMemo<CaseSummary[]>(() => query.data ?? [], [query.data]);

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
    <div className="space-y-4 p-6">
      {/* ARCH36-S1:policies-link — the tolerance editor had a route and no way in. */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className={PAGE_TITLE}>Invoice matching</h1>
        <Link
          to="./policies"
          className="rounded-lg border border-border px-3 py-1.5 text-xs font-semibold text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          Tolerance policies
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((filter, index) => (
          <button
            key={filter.label}
            type="button"
            onClick={() => setFilterIndex(index)}
            aria-pressed={index === filterIndex}
            className={`rounded-full border px-3 py-1 text-xs transition ${
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
          <table className="w-full text-sm">
            <thead className={TABLE_HEAD}>
              <tr>
                <th className="px-4 py-2 text-left">Status</th>
                <th className="px-4 py-2 text-left">Vendor</th>
                <th className="px-4 py-2 text-right">Lines</th>
                <th className="px-4 py-2 text-right">Exceptions</th>
                <th className="px-4 py-2 text-right">Variance</th>
                <th className="px-4 py-2 text-left">Scored</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {cases.map((row) => (
                <tr key={row.id} className={TABLE_ROW}>
                  <td className="px-4 py-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs ${statusTone(row.status)}`}
                    >
                      {row.status.replace("_", " ").toLowerCase()}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {row.vendor_key ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-right">{row.line_count}</td>
                  <td className="px-4 py-2 text-right">
                    {row.exception_count > 0 ? (
                      <span className="text-destructive">{row.exception_count}</span>
                    ) : (
                      <span className="text-muted-foreground">0</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right tabular-nums">
                    {formatMicros(row.variance_micros, currency)}
                  </td>
                  {/* Every timestamp goes through displayTime, never toLocaleString. */}
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    {formatTimestamp(row.created_at)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <Link
                      to={`./${row.id}`}
                      className="text-xs text-primary hover:underline"
                    >
                      Review
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default CaseQueue;
