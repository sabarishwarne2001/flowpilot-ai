/** ARCH47-S2:posting-table — the ledger rows: one posting per (target, object, approved outcome). */
import React from "react";
import { Link, useParams } from "react-router-dom";

import { PostingStateBadge, money } from "@/components/erp/common";
import { HINT, SCROLL_X, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { erpPostingPath, workItemDetailsPath } from "@/routes/tenantPaths";
import { OBJECT_LABELS, SOURCE_LABELS, type PostingRow } from "@/types/erp";
import { formatTimestamp } from "@/utils/displayTime";

export const PostingTable: React.FC<{ readonly rows: readonly PostingRow[]; readonly showTarget?: boolean }> = ({
  rows, showTarget = true,
}) => {
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  if (rows.length === 0) {
    return <p className={HINT}>No postings.</p>;
  }
  return (
    <div className={SCROLL_X}>
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className={TABLE_HEAD}>
            <th className="px-2 py-2">Object</th>
            <th className="px-2 py-2">Number</th>
            {showTarget ? <th className="px-2 py-2">Target</th> : null}
            <th className="px-2 py-2">Amount</th>
            <th className="px-2 py-2">State</th>
            <th className="px-2 py-2">ERP reference</th>
            <th className="px-2 py-2">From</th>
            <th className="px-2 py-2">Updated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className={TABLE_ROW}>
              <td className="px-2 py-2">
                <Link className="font-semibold text-primary hover:underline" to={erpPostingPath(orgSlug, workspaceSlug, row.id)}>
                  {OBJECT_LABELS[row.object_kind]}
                </Link>
              </td>
              <td className="px-2 py-2 font-mono text-xs">{row.document_number ?? "—"}</td>
              {showTarget ? <td className="px-2 py-2">{row.target_name}</td> : null}
              <td className="px-2 py-2 tabular-nums">{money(row.amount, row.currency)}</td>
              <td className="px-2 py-2">
                <PostingStateBadge state={row.state} />
                {row.last_error && row.state !== "DONE" ? (
                  <p className="mt-0.5 line-clamp-2 max-w-xs text-[11px] text-muted-foreground" title={row.last_error}>{row.last_error}</p>
                ) : null}
              </td>
              <td className="px-2 py-2 font-mono text-xs">{row.external_id ?? "—"}</td>
              <td className="px-2 py-2 text-xs">
                {SOURCE_LABELS[row.source_kind]}
                {row.work_item_id ? (
                  <>
                    {" · "}
                    <Link className="hover:underline" to={workItemDetailsPath(orgSlug, workspaceSlug, row.work_item_id)}>
                      {row.work_item_filename ?? "document"}
                    </Link>
                  </>
                ) : null}
              </td>
              <td className="px-2 py-2 text-xs text-muted-foreground">{formatTimestamp(row.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default PostingTable;
