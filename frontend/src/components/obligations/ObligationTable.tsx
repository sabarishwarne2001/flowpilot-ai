/**
 * ARCH46-S2:obligation-table — obligations as rows: when each is due (in the
 * workspace's local dates), what it is, who owns it, the party (its ARCH-42
 * record when resolved) and the document it was read from. Shared by the
 * Obligations page, Work Item details and Entity 360.
 */
import React from "react";
import { Link } from "react-router-dom";
import { Repeat } from "lucide-react";

import { SCROLL_X, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { entityPath, obligationPath, workItemDetailsPath } from "@/routes/tenantPaths";
import {
  KIND_LABELS, REVIEW_LABELS, STATE_LABELS, STATE_TONE, dueLabel, formatDay, type ObligationRow,
} from "@/types/obligations";

interface Props {
  readonly rows: readonly ObligationRow[];
  readonly orgSlug: string;
  readonly workspaceSlug: string;
  readonly hideDocument?: boolean;
  readonly hideParty?: boolean;
  readonly empty?: string;
}

export const ObligationTable: React.FC<Props> = ({ rows, orgSlug, workspaceSlug, hideDocument, hideParty, empty }) => (
  <div className={SCROLL_X}>
    <table className="w-full text-sm">
      <thead>
        <tr className={TABLE_HEAD}>
          <th className="p-2">Due</th>
          <th className="p-2">Obligation</th>
          <th className="p-2">Owner</th>
          {hideParty ? null : <th className="p-2">Party</th>}
          {hideDocument ? null : <th className="p-2">Document</th>}
          <th className="p-2">State</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} className={`${TABLE_ROW} ${row.superseded ? "opacity-60" : ""}`}>
            <td className="whitespace-nowrap p-2 align-top">
              <div className="font-medium tabular-nums">{formatDay(row.due_date)}</div>
              <div className={`text-[11px] ${row.state === "OVERDUE" ? "text-destructive" : "text-muted-foreground"}`}>
                {dueLabel(row)}
              </div>
            </td>
            <td className="p-2 align-top">
              <Link className="font-medium hover:underline" to={obligationPath(orgSlug, workspaceSlug, row.id)}>
                {row.title}
              </Link>
              <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                <span className="rounded bg-muted px-1.5 py-0.5 font-semibold">{KIND_LABELS[row.kind]}</span>
                {row.recurrence_text ? (
                  <span className="inline-flex items-center gap-1">
                    <Repeat className="h-3 w-3" aria-hidden /> {row.recurrence_text}
                  </span>
                ) : null}
                {row.review === "PENDING" ? (
                  <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-semibold text-amber-700 dark:text-amber-300"
                    title={row.reasons.join("; ")}>
                    {REVIEW_LABELS.PENDING}
                  </span>
                ) : null}
                {row.review === "REJECTED" ? <span className="line-through">{REVIEW_LABELS.REJECTED}</span> : null}
                {row.superseded ? <span>no longer in the document</span> : null}
              </div>
            </td>
            <td className="p-2 align-top text-xs">{row.owner_email ?? <span className="text-muted-foreground">Unassigned</span>}</td>
            {hideParty ? null : (
              <td className="p-2 align-top text-xs">
                {row.entity_root_id ? (
                  <Link className="hover:underline" to={entityPath(orgSlug, workspaceSlug, row.entity_root_id)}>
                    {row.entity_name ?? row.counterparty_name ?? "Record"}
                  </Link>
                ) : (
                  row.counterparty_name ?? <span className="text-muted-foreground">—</span>
                )}
              </td>
            )}
            {hideDocument ? null : (
              <td className="p-2 align-top text-xs">
                {row.work_item_id ? (
                  <Link className="hover:underline" to={workItemDetailsPath(orgSlug, workspaceSlug, row.work_item_id)}>
                    {row.work_item_filename ?? "Document"}
                  </Link>
                ) : (
                  <span className="text-muted-foreground">Added by hand</span>
                )}
              </td>
            )}
            <td className="p-2 align-top">
              <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STATE_TONE[row.state]}`}>{STATE_LABELS[row.state]}</span>
            </td>
          </tr>
        ))}
        {rows.length === 0 ? (
          <tr>
            <td colSpan={6} className="p-4 text-center text-muted-foreground">{empty ?? "No obligations."}</td>
          </tr>
        ) : null}
      </tbody>
    </table>
  </div>
);

export default ObligationTable;
