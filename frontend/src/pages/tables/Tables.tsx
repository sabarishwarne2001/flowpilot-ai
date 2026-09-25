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
  if (!capability.granted) {
    return <p className={`${SURFACE} p-6 text-sm`}>Table intelligence is included on the Business and Enterprise plans.</p>;
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
        <div className={`${SURFACE} ${SCROLL_X}`}>
          <table className="w-full text-sm">
            <thead>
              <tr className={TABLE_HEAD}>
                <th className="p-2 text-left">Document</th>
                <th className="p-2">Table</th>
                <th className="p-2">Pages</th>
                <th className="p-2">Size</th>
                <th className="p-2">Checks</th>
                <th className="p-2">Confidence</th>
                <th className="p-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((row) => (
                <tr key={row.id} className={TABLE_ROW}>
                  <td className="p-2">
                    <Link className="underline" to={tablePath(orgSlug, workspaceSlug, row.id)}>{row.original_filename}</Link>
                    {row.title ? <span className="block text-xs text-muted-foreground">{row.title}</span> : null}
                  </td>
                  <td className="p-2 text-center">{row.ordinal + 1}</td>
                  <td className="p-2 text-center">{row.page_start === row.page_end ? row.page_start : `${row.page_start}–${row.page_end}`}</td>
                  <td className="p-2 text-center">{row.n_rows - row.header_rows} × {row.n_cols}</td>
                  <td className="p-2 text-center">
                    {row.checked_relations === 0 ? "—" : row.failed_checks === 0 ? `${row.checked_relations} pass` : `${row.failed_checks} fail`}
                  </td>
                  <td className="p-2 text-center">{Math.round(row.confidence * 100)}%</td>
                  <td className="p-2 text-center">
                    <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STATUS_TONE[row.status]}`}>{STATUS_LABELS[row.status]}</span>
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
