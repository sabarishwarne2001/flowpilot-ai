/**
 * Phase 1 — Batch operations: every batch in the workspace with its progress, lanes and
 * confidence; the dispatch policy; export packages; and package verification.
 */
import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Activity, Gauge, Layers, Lock, Plus, ShieldCheck, Sparkles, Target } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { CapabilityLoading } from "@/components/common/CapabilityLoading";
import { ViewPlansAction } from "@/components/billing/ViewPlansAction";
import { DispatchPolicyCard } from "@/components/batches/DispatchPolicyCard";
import { NewBatchDialog } from "@/components/batches/NewBatchDialog";
import { PackagesPanel } from "@/components/batches/PackagesPanel";
import { LaneChip, pct, SegmentedProgress, StatTile } from "@/components/batches/shared";
import { VerifyPackageDialog } from "@/components/batches/VerifyPackageDialog";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { batchPath } from "@/routes/tenantPaths";
import { batchKeys, listBatches } from "@/services/api/batches";
import { errorMessage } from "@/services/api/errors";
import { formatTimestampDate } from "@/utils/displayTime";
import type { BatchSummary } from "@/types/batches";

type Tab = "ACTIVE" | "ARCHIVED" | "PACKAGES";

const LockedView: React.FC = () => (
  <section className="fp-card mx-auto mt-6 max-w-2xl space-y-3 p-6" aria-labelledby="batches-lock">
    <div className="flex items-center gap-2">
      <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
      <h2 id="batches-lock" className="text-[15px] font-semibold tracking-tight">Batch operations</h2>
    </div>
    <p className="text-sm text-muted-foreground">
      Follow documents in batches from upload to hand-off: confidence analytics, schema self-healing, dispatch to
      straight-through, review or exception lanes, and export packages with a SHA-256 integrity manifest your
      auditors can verify.
    </p>
    <p className="text-xs text-muted-foreground">Batch operations is included on the Business and Enterprise plans.</p>
    <ViewPlansAction />
  </section>
);

const BatchRow: React.FC<{ readonly batch: BatchSummary; readonly to: string }> = ({ batch, to }) => {
  const p = batch.progress;
  return (
    <tr className="group border-b border-border/60 text-sm last:border-0 hover:bg-muted/40" data-testid="batch-row">
      <td className="max-w-[18rem] px-4 py-3">
        <Link to={to} className="block truncate font-medium text-foreground hover:text-primary" title={batch.name}>
          {batch.name}
        </Link>
        <span className="mt-0.5 block text-xs text-muted-foreground">
          {batch.source === "INGESTION" ? "From an upload" : "From a selection"} · {formatTimestampDate(batch.created_at)}
        </span>
      </td>
      <td className="w-[22%] min-w-[10rem] px-4 py-3">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span className="fp-num">{p.completed + p.failed} / {p.documents}</span>
          <span className="fp-num">{p.percent.toFixed(0)}%</span>
        </div>
        <div className="mt-1.5">
          <SegmentedProgress
            completed={p.completed}
            failed={p.failed}
            processing={p.processing}
            queued={p.queued}
            label={`${batch.name} progress`}
          />
        </div>
      </td>
      <td className="px-4 py-3">
        {batch.dispatched_at ? (
          <div className="flex flex-wrap gap-1">
            <LaneChip lane="STRAIGHT_THROUGH" count={p.straight_through} compact />
            <LaneChip lane="REVIEW" count={p.review} compact />
            <LaneChip lane="EXCEPTION" count={p.exception} compact />
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">Not dispatched</span>
        )}
      </td>
      <td className="fp-num whitespace-nowrap px-4 py-3 text-right text-sm">{pct(p.mean_confidence)}</td>
    </tr>
  );
};

const Batches: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const role = workspace?.role;
  const canAct = role === "OWNER" || role === "ADMIN" || role === "CONTRIBUTOR";
  const isAdmin = role === "OWNER" || role === "ADMIN";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.batchDispatch);
  const [tab, setTab] = useState<Tab>("ACTIVE");
  const [creating, setCreating] = useState(false);
  const [verifying, setVerifying] = useState(false);

  const status = tab === "ARCHIVED" ? "ARCHIVED" : "ACTIVE";
  const query = useQuery({
    queryKey: batchKeys.list(workspaceId, status),
    queryFn: () => listBatches(workspaceId, status),
    enabled: Boolean(workspaceId && capability.granted) && tab !== "PACKAGES",
    refetchInterval: (q) =>
      q.state.data?.items.some((b) => b.progress.queued + b.progress.processing > 0) ? 3_000 : false,
  });

  const totals = useMemo(() => {
    const items = query.data?.items ?? [];
    const sum = (pick: (b: BatchSummary) => number) => items.reduce((acc, b) => acc + pick(b), 0);
    const dispatched = sum((b) => b.progress.straight_through + b.progress.review + b.progress.exception);
    const scored = items.filter((b) => b.progress.mean_confidence !== null);
    return {
      batches: items.length,
      documents: sum((b) => b.progress.documents),
      inFlight: sum((b) => b.progress.queued + b.progress.processing),
      failed: sum((b) => b.progress.failed),
      straightThrough: dispatched ? sum((b) => b.progress.straight_through) / dispatched : null,
      confidence: scored.length
        ? scored.reduce((acc, b) => acc + (b.progress.mean_confidence ?? 0) * b.progress.documents, 0) /
          Math.max(scored.reduce((acc, b) => acc + b.progress.documents, 0), 1)
        : null,
    };
  }, [query.data]);

  if (capability.isLoading) {
    return <CapabilityLoading />;
  }
  if (!capability.granted) {
    return <LockedView />;
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-gradient-to-b from-muted to-muted/40 text-primary shadow-inner-highlight">
            <Layers className="h-5 w-5" aria-hidden />
          </span>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Batch operations</h1>
            <p className="mt-0.5 max-w-2xl text-[13px] text-muted-foreground">
              Follow documents in batches from upload to hand-off: confidence, schema health, dispatch lanes, and
              export packages with a SHA-256 manifest.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="fp-btn fp-btn-secondary h-9 text-[13px]" onClick={() => setVerifying(true)}>
            <ShieldCheck className="h-4 w-4" aria-hidden /> Verify a package
          </button>
          {canAct ? (
            <button type="button" className="fp-btn fp-btn-primary h-9 text-[13px]" onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" aria-hidden /> New batch
            </button>
          ) : null}
        </div>
      </header>

      <section aria-label="Batch totals" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatTile label="Active batches" value={totals.batches} hint={`${totals.documents} documents`} icon={Layers} testId="kpi-batches" />
        <StatTile
          label="In flight"
          value={totals.inFlight}
          hint={totals.failed ? `${totals.failed} failed and waiting for a retry` : "Queued or processing now"}
          icon={Activity}
        />
        <StatTile label="Straight-through rate" value={pct(totals.straightThrough)} hint="Of dispatched documents" icon={Target} />
        <StatTile label="Mean confidence" value={pct(totals.confidence)} hint="Verified documents, weighted by batch size" icon={Gauge} />
      </section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-4">
          <div className="flex gap-1 border-b border-border" role="tablist" aria-label="Batch views">
            {([["ACTIVE", "Active"], ["ARCHIVED", "Archived"], ["PACKAGES", "Export packages"]] as const).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={tab === value}
                onClick={() => setTab(value)}
                className={`relative px-3 py-2 text-[13px] font-medium after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded-full ${
                  tab === value ? "text-foreground after:bg-primary" : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "PACKAGES" ? (
            <PackagesPanel workspaceId={workspaceId} canCreate={false} canDownload={canAct} />
          ) : query.isLoading ? (
            <div className="fp-card h-48 animate-pulse" />
          ) : query.isError ? (
            <p className="text-sm text-destructive">{errorMessage(query.error, "Batches could not be loaded.")}</p>
          ) : (query.data?.items.length ?? 0) === 0 ? (
            <div className="fp-card flex flex-col items-center gap-3 px-6 py-12 text-center">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl border border-border bg-muted/60 text-muted-foreground">
                <Sparkles className="h-5 w-5" aria-hidden />
              </span>
              <p className="text-sm font-semibold">{tab === "ARCHIVED" ? "No archived batches" : "No batches yet"}</p>
              <p className="max-w-md text-xs leading-relaxed text-muted-foreground">
                {tab === "ARCHIVED"
                  ? "Archive a batch when its work is done; it keeps its lanes and packages."
                  : "Create a batch from recent documents, or select documents on the Documents page and choose Add to batch."}
              </p>
              {canAct && tab === "ACTIVE" ? (
                <button type="button" className="fp-btn fp-btn-primary h-8 text-xs" onClick={() => setCreating(true)}>
                  <Plus className="h-3.5 w-3.5" aria-hidden /> New batch
                </button>
              ) : null}
            </div>
          ) : (
            <div className="fp-card overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse text-left">
                <thead>
                  <tr className="sticky top-0 border-b border-border bg-muted/40 text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
                    <th className="px-4 py-2.5">Batch</th>
                    <th className="px-4 py-2.5">Progress</th>
                    <th className="px-4 py-2.5">Lanes</th>
                    <th className="px-4 py-2.5 text-right">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data?.items.map((batch) => (
                    <BatchRow key={batch.id} batch={batch} to={batchPath(orgSlug, workspaceSlug, batch.id)} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        <aside className="space-y-4">
          <DispatchPolicyCard workspaceId={workspaceId} canEdit={isAdmin} />
        </aside>
      </div>

      {creating ? (
        <NewBatchDialog
          workspaceId={workspaceId}
          detailPath={(id) => batchPath(orgSlug, workspaceSlug, id)}
          onClose={() => setCreating(false)}
        />
      ) : null}
      {verifying ? <VerifyPackageDialog workspaceId={workspaceId} onClose={() => setVerifying(false)} /> : null}
    </div>
  );
};

export default Batches;
