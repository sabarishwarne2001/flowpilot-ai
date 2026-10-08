/** ARCH44-S2:page-tables — every table extracted in the workspace, newest first, filterable by status. */
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Loader2, Table2 } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { HINT, PAGE_TITLE, SCROLL_X, SURFACE, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { tablePath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { listTables, tableKeys } from "@/services/api/tables";
import { STATUS_LABELS, STATUS_TONE, type TableStatus } from "@/types/tables";
import { CapabilityLoading } from "@/components/common/CapabilityLoading";
import { ViewPlansAction } from "@/components/billing/ViewPlansAction";

const FILTERS: readonly { readonly id: TableStatus | undefined; readonly label: string }[] = [
  { id: undefined, label: "All" },
  { id: "FLAGGED", label: "Needs review" },
  { id: "VALIDATED", label: "Reconciles" },
  { id: "EXTRACTED", label: "Extracted" },
  { id: "REVIEWED", label: "Reviewed" },
];

const Tables: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.tableIntelligence);
  const [status, setStatus] = useState<TableStatus | undefined>(undefined);
  const query = useQuery({
    queryKey: tableKeys.list(workspaceId, status),
    queryFn: () => listTables(workspaceId, status),
    enabled: Boolean(workspaceId && capability.granted),
  });
  // F-164: no lock verdict while the plan is still being read.
  if (capability.isLoading) {return <CapabilityLoading />;}
  if (!capability.granted) {
    return <div className={`${SURFACE} p-6 text-sm`}><p>Table intelligence is included on the Business and Enterprise plans.</p><ViewPlansAction /></div>;
  }
  return (
    <div className="space-y-4 p-4">
      <header className="flex items-center gap-2">
        <Table2 className="h-5 w-5" aria-hidden />
        <h1 className={PAGE_TITLE}>Tables</h1>
      </header>
      <p className={HINT}>
        Tables found in processed documents — statements, ledgers, line items, charts — with every figure checked against
        the arithmetic it has to satisfy.
      </p>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter by status">
        {FILTERS.map((f) => (
          <button
            key={f.label}
            type="button"
            role="tab"
            aria-selected={status === f.id}
            onClick={() => setStatus(f.id)}
            className={`rounded-full border px-3 py-1 text-xs font-semibold ${status === f.id ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted"}`}
          >
            {f.label}
            {query.data && f.id ? ` (${query.data.counts_by_status[f.id] ?? 0})` : ""}
          </button>
        ))}
      </div>
      {query.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
      {query.isError ? <p className="text-sm text-destructive">{errorMessage(query.error, "Something went wrong.")}</p> : null}
      {query.data ? (
        <div className={`${SURFACE} ${SCROLL_X} max-h-[75vh] overflow-y-auto`}>
          <table className="w-full min-w-[720px] text-sm">
            <thead className="sticky top-0 z-[1] bg-card">
              <tr className={TABLE_HEAD}>
                <th className="px-3 py-2.5 text-left">Document</th>
                <th className="px-3 py-2.5 text-right">Table</th>
                <th className="px-3 py-2.5 text-right">Pages</th>
                <th className="px-3 py-2.5 text-right">Size</th>
                <th className="px-3 py-2.5 text-left">Checks</th>
                <th className="px-3 py-2.5 text-right">Confidence</th>
                <th className="px-3 py-2.5 text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((row) => (
                <tr key={row.id} className={TABLE_ROW}>
                  <td className="max-w-[18rem] px-3 py-2.5">
                    <Link className="block truncate font-medium text-foreground hover:text-primary" title={row.original_filename} to={tablePath(orgSlug, workspaceSlug, row.id)}>
                      {row.original_filename}
                    </Link>
                    {row.title ? <span className="block truncate text-xs text-muted-foreground" title={row.title}>{row.title}</span> : null}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{row.ordinal + 1}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{row.page_start === row.page_end ? row.page_start : `${row.page_start}–${row.page_end}`}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{row.n_rows - row.header_rows} × {row.n_cols}</td>
                  <td className="px-3 py-2.5">
                    {row.checked_relations === 0 ? (
                      <span className="text-muted-foreground">—</span>
                    ) : row.failed_checks === 0 ? (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
                        <span aria-hidden>✓</span> {row.checked_relations} pass
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-destructive">
                        <span aria-hidden>!</span> {row.failed_checks} fail
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{Math.round(row.confidence * 100)}%</td>
                  <td className="px-3 py-2.5">
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_TONE[row.status]}`}>{STATUS_LABELS[row.status]}</span>
                  </td>
                </tr>
              ))}
              {query.data.items.length === 0 ? (
                <tr><td colSpan={7} className="p-4 text-center text-muted-foreground">No tables yet.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
};

export default Tables;
