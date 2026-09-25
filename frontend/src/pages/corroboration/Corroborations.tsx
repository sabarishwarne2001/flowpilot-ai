/**
 * ARCH45-S2:page-corroborations — every comparison in the workspace, newest
 * first, filterable by status, and the form that starts a new one. Opening
 * this page with ?with=<work item id> (from Work Item details) starts a new
 * comparison with that document as the baseline.
 */
import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { GitCompare, Loader2, Lock, Plus } from "lucide-react";

import NewComparison from "@/components/corroboration/NewComparison";
import { CAPABILITY } from "@/constants/capabilities";
import { BUTTON_PRIMARY, HINT, PAGE_TITLE, SCROLL_X, SECTION_TITLE, SURFACE, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { corroborationPath } from "@/routes/tenantPaths";
import { corroborationKeys, listRuns } from "@/services/api/corroboration";
import { errorMessage } from "@/services/api/errors";
import { getWorkItemDetails } from "@/services/api/workItem";
import { STATUS_LABELS, STATUS_TONE, isPending, type RequestResult, type RunStatus } from "@/types/corroboration";
import { formatDateTime } from "@/utils/formatters";

const FILTERS: readonly { readonly id: RunStatus | undefined; readonly label: string }[] = [
  { id: undefined, label: "All" },
  { id: "COMPLETED", label: "Current" },
  { id: "STALE", label: "Out of date" },
  { id: "QUEUED", label: "Queued" },
  { id: "RUNNING", label: "Comparing" },
  { id: "FAILED", label: "Failed" },
];

const Corroborations: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const withId = params.get("with");
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.universalCorroborator);
  const canCompare = workspace?.role === "ADMIN" || workspace?.role === "OWNER" || workspace?.role === "CONTRIBUTOR";
  const [status, setStatus] = useState<RunStatus | undefined>(undefined);
  const [composing, setComposing] = useState(false);
  const query = useQuery({
    queryKey: corroborationKeys.list(workspaceId, status),
    queryFn: () => listRuns(workspaceId, status),
    enabled: Boolean(workspaceId && capability.granted),
    refetchInterval: (q) => (q.state.data?.items.some((r) => isPending(r.status)) ? 3000 : false),
  });
  const baseline = useQuery({
    queryKey: ["corroboration", workspaceId, "baseline", withId ?? ""],
    queryFn: () => getWorkItemDetails(workspaceId, withId ?? ""),
    enabled: Boolean(workspaceId && withId && capability.granted && canCompare),
  });
  const initial = useMemo(
    () => (withId && baseline.data ? [{ id: withId, label: baseline.data.original_filename }] : []),
    [withId, baseline.data],
  );
  const showForm = canCompare && (composing || Boolean(withId && baseline.data));
  const close = (): void => {
    setComposing(false);
    if (withId) {
      params.delete("with");
      setParams(params, { replace: true });
    }
  };
  const started = (result: RequestResult): void => {
    navigate(corroborationPath(orgSlug, workspaceSlug, result.run.id));
  };

  if (!capability.granted) {
    return (
      <section className={`${SURFACE} mx-auto mt-6 max-w-2xl space-y-3 p-6`} aria-labelledby="corroboration-lock">
        <div className="flex items-center gap-2">
          <Lock className="h-4 w-4" aria-hidden />
          <h2 id="corroboration-lock" className={SECTION_TITLE}>Document corroborator</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          Compare two to five documents — a contract and its amendment, a purchase order, invoice and delivery note, a
          policy and a claim — clause by clause, field by field and line by line, with every material difference ranked
          and a PDF report of what disagrees.
        </p>
        <p className={HINT}>It&apos;s included on the Enterprise plan.</p>
      </section>
    );
  }
  return (
    <div className="space-y-4 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <GitCompare className="h-5 w-5" aria-hidden />
        <h1 className={PAGE_TITLE}>Document corroborator</h1>
        {canCompare && !showForm ? (
          <button type="button" className={`${BUTTON_PRIMARY} ml-auto`} onClick={() => setComposing(true)}>
            <Plus className="h-4 w-4" aria-hidden /> New comparison
          </button>
        ) : null}
      </header>
      <p className={HINT}>
        Clauses are aligned by meaning, not position, so a renumbered or reflowed version still lines up; what differs is
        ranked by how much it matters — a changed payment term above a reworded sentence.
      </p>
      {withId && baseline.isError ? (
        <p className="text-sm text-destructive">{errorMessage(baseline.error, "That document could not be loaded.")}</p>
      ) : null}
      {showForm ? (
        <NewComparison key={withId ?? "new"} workspaceId={workspaceId} initial={initial} onStarted={started} onCancel={close} />
      ) : null}
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
                <th className="p-2 text-left">Documents</th>
                <th className="p-2">Differences</th>
                <th className="p-2">Material</th>
                <th className="p-2">Highest</th>
                <th className="p-2">Compared</th>
                <th className="p-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((run) => (
                <tr key={run.id} className={TABLE_ROW}>
                  <td className="p-2">
                    <Link className="underline" to={corroborationPath(orgSlug, workspaceSlug, run.id)}>
                      {run.documents.map((d) => d.label).join(" · ") || `${run.document_count} documents`}
                    </Link>
                  </td>
                  <td className="p-2 text-center tabular-nums">{isPending(run.status) ? "—" : run.discrepancy_count}</td>
                  <td className="p-2 text-center tabular-nums">
                    {isPending(run.status) ? "—" : run.material_count}
                    {run.open_material_count > 0 ? <span className="block text-[10px] text-muted-foreground">{run.open_material_count} open</span> : null}
                  </td>
                  <td className="p-2 text-center tabular-nums">{isPending(run.status) ? "—" : run.max_materiality.toFixed(2)}</td>
                  <td className="p-2 text-center text-xs text-muted-foreground">{formatDateTime(run.completed_at ?? run.created_at)}</td>
                  <td className="p-2 text-center">
                    <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STATUS_TONE[run.status]}`}>{STATUS_LABELS[run.status]}</span>
                  </td>
                </tr>
              ))}
              {query.data.items.length === 0 ? (
                <tr><td colSpan={6} className="p-4 text-center text-muted-foreground">No comparisons yet.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
};

export default Corroborations;
