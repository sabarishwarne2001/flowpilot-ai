/**
 * ARCH49-S2:agent-inbox — the exception agent's proposals, newest first:
 * filter by status, open one to see its evidence, tool calls and the fenced
 * excerpts behind it, and approve, reject or undo.
 */
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { ProposalStatusBadge, pct } from "@/components/process/common";
import { ProposalPanel } from "@/components/process/ProposalPanel";
import { HINT, SURFACE } from "@/components/ui/primitives";
import { casePath, processProposalPath, verificationPath } from "@/routes/tenantPaths";
import { listProposals, processKeys, type ProposalFilters } from "@/services/api/process";
import { errorMessage } from "@/services/api/errors";
import { formatTimestamp } from "@/utils/displayTime";
import { KIND_LABELS, type ReviewKind } from "@/types/review";
import { STATUS_LABELS, type ProposalStatus } from "@/types/process";

const FILTERS: readonly { readonly id: string; readonly label: string; readonly statuses: readonly ProposalStatus[] }[] = [
  { id: "PROPOSED,AUTO_SCHEDULED", label: "Waiting", statuses: ["PROPOSED", "AUTO_SCHEDULED"] },
  { id: "AUTO_SCHEDULED", label: STATUS_LABELS.AUTO_SCHEDULED, statuses: ["AUTO_SCHEDULED"] },
  { id: "APPLIED,AUTO_APPLIED", label: "Applied", statuses: ["APPLIED", "AUTO_APPLIED"] },
  { id: "REJECTED,UNDONE", label: "Rejected or undone", statuses: ["REJECTED", "UNDONE"] },
  { id: "SUPERSEDED,FAILED", label: "Overtaken or refused", statuses: ["SUPERSEDED", "FAILED"] },
  { id: "", label: "All", statuses: [] },
];

const PAGE = 50;

export const AgentInbox: React.FC<{
  readonly workspaceId: string;
  readonly canAct: boolean;
  readonly selectedId?: string | undefined;
  readonly onSelect: (id: string | null) => void;
}> = ({ workspaceId, canAct, selectedId, onSelect }) => {
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const [status, setStatus] = useState("PROPOSED,AUTO_SCHEDULED");
  const [offset, setOffset] = useState(0);
  const filters: ProposalFilters = { ...(status ? { status } : {}), limit: PAGE, offset };
  const list = useQuery({
    queryKey: processKeys.proposalList(workspaceId, filters),
    queryFn: () => listProposals(workspaceId, filters),
    enabled: Boolean(workspaceId),
    refetchInterval: 30_000,
  });
  const counts = list.data?.counts ?? {};
  const count = (statuses: readonly ProposalStatus[]): number =>
    (statuses.length ? statuses : (Object.keys(counts) as ProposalStatus[])).reduce((n, s) => n + (counts[s] ?? 0), 0);
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button key={f.id || "all"} type="button" aria-pressed={status === f.id}
              onClick={() => { setStatus(f.id); setOffset(0); }}
              className={`rounded-full border px-3 py-1 text-xs font-semibold ${status === f.id ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted"}`}>
              {f.label} ({count(f.statuses)})
            </button>
          ))}
        </div>
        {list.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
        {list.isError ? <p className="text-sm text-destructive">{errorMessage(list.error, "Could not load proposals.")}</p> : null}
        {list.data && list.data.items.length === 0 ? (
          <p className={HINT}>Nothing here. The agent plans every open review item and incomplete or inconsistent case on each sweep.</p>
        ) : null}
        {list.data && list.data.items.length > 0 ? (
          // Phase 2: two lines per proposal instead of five columns squeezed into half the page
          // (the "When" column was cut off and every status pill wrapped).
          <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border" aria-label="Proposals">
            {list.data.items.map((p) => (
              <li
                key={p.id}
                className={`px-3 py-2 ${selectedId === p.id ? "bg-primary/5 shadow-[inset_2px_0_0_hsl(var(--primary))]" : "hover:bg-muted/30"}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <Link to={processProposalPath(orgSlug, workspaceSlug, p.id)} onClick={(e) => { e.preventDefault(); onSelect(p.id); }}
                    className="min-w-0 text-sm font-semibold text-primary hover:underline">
                    {p.label}
                  </Link>
                  <span className="shrink-0"><ProposalStatusBadge status={p.status} /></span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                  {p.subject_type === "CASE" ? (
                    <Link className="font-medium text-foreground/80 hover:underline" to={casePath(orgSlug, workspaceSlug, p.subject_id)}>Case</Link>
                  ) : (
                    <Link className="font-medium text-foreground/80 hover:underline" to={verificationPath(orgSlug, workspaceSlug)}>
                      {KIND_LABELS[p.subject_kind as ReviewKind] ?? p.subject_kind}
                    </Link>
                  )}
                  <span aria-hidden>·</span>
                  <span className="tabular-nums">
                    {pct(p.confidence)} confident
                    {p.calibrated_probability !== null ? ` (calibrated ${pct(p.calibrated_probability)})` : ""}
                  </span>
                  <span aria-hidden>·</span>
                  <span>{formatTimestamp(p.created_at)}</span>
                  {p.verdict ? <span className="rounded bg-muted px-1 text-[10.5px] font-semibold">{p.verdict}</span> : null}
                  {p.injection_suspected ? <span className="rounded bg-destructive/10 px-1 text-[10.5px] font-semibold text-destructive">injection?</span> : null}
                </div>
              </li>
            ))}
          </ul>
        ) : null}
        {list.data && list.data.total > PAGE ? (
          <nav className="flex items-center justify-between text-xs text-muted-foreground" aria-label="Pages">
            <span>{list.data.total} proposal(s)</span>
            <span className="flex gap-2">
              <button type="button" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE))}
                className="rounded border border-border px-2 py-1 disabled:opacity-40">Previous</button>
              <button type="button" disabled={offset + PAGE >= list.data.total} onClick={() => setOffset((o) => o + PAGE)}
                className="rounded border border-border px-2 py-1 disabled:opacity-40">Next</button>
            </span>
          </nav>
        ) : null}
      </div>
      <div className={`${SURFACE} self-start p-4 lg:sticky lg:top-4`}>
        {selectedId ? (
          <ProposalPanel workspaceId={workspaceId} proposalId={selectedId} canAct={canAct} onClose={() => onSelect(null)} />
        ) : (
          <p className={HINT}>Open a proposal to see what the agent read, what it would do, and why.</p>
        )}
      </div>
    </div>
  );
};

export default AgentInbox;
