/**
 * Phase 1 — one batch: where its documents are, how sure the extraction is, which fields drifted
 * from their schema, where each document goes next, and its export packages.
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  Archive,
  ArchiveRestore,
  ArrowLeft,
  ArrowRight,
  Gauge,
  Loader2,
  RefreshCw,
  Route as RouteIcon,
  Send,
  Timer,
  Trash2,
  Wand2,
  X,
} from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { CapabilityLoading } from "@/components/common/CapabilityLoading";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { ViewPlansAction } from "@/components/billing/ViewPlansAction";
import { ConfidenceHistogram } from "@/components/batches/ConfidenceHistogram";
import { PackagesPanel } from "@/components/batches/PackagesPanel";
import { formatSeconds, LaneChip, pct, SegmentedProgress, StatTile } from "@/components/batches/shared";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { batchesPath, workItemDetailsPath } from "@/routes/tenantPaths";
import {
  batchKeys,
  deleteBatch,
  dispatchBatch,
  getBatch,
  getBatchAnalytics,
  healBatch,
  removeBatchDocument,
  retryFailed,
  updateBatch,
} from "@/services/api/batches";
import { errorMessage } from "@/services/api/errors";
import { formatTimestamp } from "@/utils/displayTime";
import {
  SCHEMA_STATE_LABELS,
  type BatchDocument,
  type HealResult,
  type Lane,
  type SchemaState,
} from "@/types/batches";

const SCHEMA_TONE: Readonly<Record<SchemaState, string>> = {
  HEALTHY: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
  HEALABLE: "border-primary/25 bg-primary/[0.08] text-primary",
  NEEDS_ATTENTION: "border-amber-500/35 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  NO_SCHEMA: "border-border bg-muted/50 text-muted-foreground",
};

const show = (value: unknown): string => {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : JSON.stringify(value);
};

const SchemaChip: React.FC<{ readonly state: SchemaState }> = ({ state }) => (
  <span className={`inline-flex whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold ${SCHEMA_TONE[state]}`}>
    {SCHEMA_STATE_LABELS[state]}
  </span>
);

const LANES: readonly Lane[] = ["STRAIGHT_THROUGH", "REVIEW", "EXCEPTION"];

const LaneBoard: React.FC<{ readonly documents: readonly BatchDocument[]; readonly linkTo: (id: string) => string }> = ({
  documents,
  linkTo,
}) => {
  const [expanded, setExpanded] = useState<Lane | null>(null);
  const pending = documents.filter((d) => d.lane === null);
  return (
    <section aria-labelledby="lanes-title" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="lanes-title" className="text-[15px] font-semibold tracking-tight">Dispatch lanes</h2>
        <p className="text-xs text-muted-foreground">
          Where each finished document goes now, under the current policy.
          {pending.length ? ` ${pending.length} still processing.` : ""}
        </p>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {LANES.map((lane) => {
          const docs = documents.filter((d) => d.lane === lane);
          const visible = expanded === lane ? docs : docs.slice(0, 6);
          return (
            <div key={lane} className="fp-card flex min-h-[9rem] flex-col overflow-hidden" data-testid={`lane-${lane}`}>
              <header className="flex items-center justify-between border-b border-border bg-muted/30 px-3 py-2">
                <LaneChip lane={lane} />
                <span className="fp-num text-sm font-semibold" aria-label={`${docs.length} documents`}>{docs.length}</span>
              </header>
              {docs.length === 0 ? (
                <p className="flex flex-1 items-center justify-center px-3 py-6 text-center text-xs text-muted-foreground">Nothing here.</p>
              ) : (
                <ul className="divide-y divide-border/60">
                  {visible.map((d) => (
                    <li key={d.work_item_id} className="px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <Link to={linkTo(d.work_item_id)} className="min-w-0 truncate text-[13px] font-medium hover:text-primary" title={d.original_filename}>
                          {d.original_filename}
                        </Link>
                        <span className="fp-num shrink-0 text-xs text-muted-foreground">{pct(d.confidence)}</span>
                      </div>
                      {d.reasons[0] ? (
                        <p className="mt-0.5 line-clamp-2 text-[11.5px] leading-snug text-muted-foreground" title={d.reasons.join(" ")}>
                          {d.reasons[0]}
                          {d.reasons.length > 1 ? ` (+${d.reasons.length - 1} more)` : ""}
                        </p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
              {docs.length > 6 ? (
                <button
                  type="button"
                  className="mt-auto border-t border-border px-3 py-1.5 text-left text-xs font-medium text-muted-foreground hover:text-foreground"
                  onClick={() => setExpanded(expanded === lane ? null : lane)}
                >
                  {expanded === lane ? "Show fewer" : `Show all ${docs.length}`}
                </button>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
};

const ChangeList: React.FC<{ readonly doc: BatchDocument }> = ({ doc }) => (
  <ul className="space-y-1">
    {doc.changes.map((change, index) => (
      <li key={`${change.kind}-${change.field}-${index}`} className="flex flex-wrap items-center gap-1 text-xs">
        {change.kind === "RENAME" ? (
          <>
            <code className="rounded bg-muted px-1 py-px font-mono text-[11px] text-muted-foreground line-through decoration-muted-foreground/60">{change.from_field}</code>
            <ArrowRight className="h-3 w-3 text-muted-foreground" aria-label="renamed to" />
            <code className="rounded bg-primary/10 px-1 py-px font-mono text-[11px] text-primary">{change.field}</code>
          </>
        ) : (
          <>
            <code className="rounded bg-muted px-1 py-px font-mono text-[11px]">{change.field}</code>
            <span className="text-muted-foreground">{show(change.before)}</span>
            <ArrowRight className="h-3 w-3 text-muted-foreground" aria-label="becomes" />
            <span className="font-medium">{show(change.after)}</span>
          </>
        )}
      </li>
    ))}
    {doc.issues.map((issue, index) => (
      <li key={`issue-${issue.field}-${index}`} className="text-xs text-amber-800 dark:text-amber-300">
        {issue.message}
      </li>
    ))}
  </ul>
);

const BatchDetail: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const role = workspace?.role;
  const canAct = role === "OWNER" || role === "ADMIN" || role === "CONTRIBUTOR";
  const { orgSlug = "", workspaceSlug = "", batchId = "" } = useParams<{ orgSlug: string; workspaceSlug: string; batchId: string }>();
  const navigate = useNavigate();
  const client = useQueryClient();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.batchDispatch);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [healReport, setHealReport] = useState<HealResult | null>(null);
  const enabled = Boolean(workspaceId && batchId && capability.granted);

  const detail = useQuery({
    queryKey: batchKeys.detail(workspaceId, batchId),
    queryFn: () => getBatch(workspaceId, batchId),
    enabled,
    refetchInterval: (q) =>
      q.state.data?.documents.some((d) => d.status === "QUEUED" || d.status === "PROCESSING") ? 3_000 : false,
  });
  const analytics = useQuery({
    queryKey: batchKeys.analytics(workspaceId, batchId),
    queryFn: () => getBatchAnalytics(workspaceId, batchId),
    enabled,
    refetchInterval: (q) => ((q.state.data?.status.queued ?? 0) + (q.state.data?.status.processing ?? 0) > 0 ? 3_000 : false),
  });

  const refresh = async (): Promise<void> => {
    await client.invalidateQueries({ queryKey: batchKeys.all(workspaceId) });
  };

  const heal = useMutation({
    mutationFn: () => healBatch(workspaceId, batchId),
    onSuccess: async (result) => {
      setHealReport(result);
      if (result.healed) {
        toast.success(`${result.healed} document${result.healed === 1 ? "" : "s"} healed to their schema.`);
      } else {
        toast.info("No document was changed. The list below says why.");
      }
      await refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Schema healing failed.")),
  });
  const dispatch = useMutation({
    mutationFn: () => dispatchBatch(workspaceId, batchId),
    onSuccess: async (result) => {
      toast.success(
        `Dispatched: ${result.straight_through} straight through, ${result.review} to review, ${result.exception} exceptions${
          result.pending ? `, ${result.pending} still processing` : ""
        }.`,
      );
      await refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Dispatch failed.")),
  });
  const retry = useMutation({
    mutationFn: () => retryFailed(workspaceId, batchId),
    onSuccess: async (result) => {
      toast.success(`${result.requeued} failed document${result.requeued === 1 ? "" : "s"} queued for processing again.`);
      await refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Retry failed.")),
  });
  const archive = useMutation({
    mutationFn: (status: "ACTIVE" | "ARCHIVED") => updateBatch(workspaceId, batchId, { status }),
    onSuccess: async (batch) => {
      toast.success(batch.status === "ARCHIVED" ? "Batch archived." : "Batch restored.");
      await refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "The batch could not be updated.")),
  });
  const remove = useMutation({
    mutationFn: () => deleteBatch(workspaceId, batchId),
    onSuccess: async () => {
      toast.success("Batch deleted. Its documents were not touched.");
      await refresh();
      navigate(batchesPath(orgSlug, workspaceSlug));
    },
    onError: (error) => toast.error(errorMessage(error, "The batch could not be deleted.")),
  });
  const removeDoc = useMutation({
    mutationFn: (workItemId: string) => removeBatchDocument(workspaceId, batchId, workItemId),
    onSuccess: async () => {
      toast.success("Removed from the batch.");
      await refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "The document could not be removed.")),
  });

  const docs = useMemo(() => detail.data?.documents ?? [], [detail.data]);
  const healable = docs.filter((d) => d.changes.length > 0);
  const attention = docs.filter((d) => d.schema_state === "NEEDS_ATTENTION" || d.schema_state === "HEALABLE");
  const failed = docs.filter((d) => d.status === "FAILED").length;
  const busy = heal.isPending || dispatch.isPending || retry.isPending || archive.isPending;
  const linkTo = (id: string): string => workItemDetailsPath(orgSlug, workspaceSlug, id);

  if (capability.isLoading) {
    return <CapabilityLoading />;
  }
  if (!capability.granted) {
    return (
      <div className="fp-card m-4 space-y-2 p-6 text-sm">
        <p>Batch operations is included on the Business and Enterprise plans.</p>
        <ViewPlansAction />
      </div>
    );
  }
  if (detail.isLoading) {
    return <CapabilityLoading />;
  }
  if (detail.isError || !detail.data) {
    return (
      <div className="space-y-3 p-4">
        <Link to={batchesPath(orgSlug, workspaceSlug)} className="fp-btn fp-btn-ghost -ml-2.5"><ArrowLeft className="h-4 w-4" /> Batch operations</Link>
        <p className="text-sm text-destructive">{errorMessage(detail.error, "This batch could not be loaded.")}</p>
      </div>
    );
  }

  const { batch, policy } = detail.data;
  const a = analytics.data;
  const archived = batch.status === "ARCHIVED";

  return (
    <div className="space-y-6">
      <Link to={batchesPath(orgSlug, workspaceSlug)} className="fp-btn fp-btn-ghost -ml-2.5">
        <ArrowLeft className="h-4 w-4" /> Batch operations
      </Link>

      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight" title={batch.name}>{batch.name}</h1>
            {archived ? (
              <span className="rounded-full border border-border bg-muted px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">Archived</span>
            ) : null}
          </div>
          <p className="mt-1 text-[13px] text-muted-foreground">
            {batch.source === "INGESTION" ? "From an upload" : "From a selection"} · created {formatTimestamp(batch.created_at)}
            {batch.dispatched_at ? ` · last dispatched ${formatTimestamp(batch.dispatched_at)}` : " · not dispatched yet"}
          </p>
          {batch.description ? <p className="mt-2 max-w-2xl text-sm">{batch.description}</p> : null}
        </div>
        {canAct ? (
          <div className="flex flex-wrap gap-2">
            {failed > 0 ? (
              <button type="button" className="fp-btn fp-btn-secondary h-9 text-[13px]" disabled={busy} onClick={() => retry.mutate()}>
                <RefreshCw className={`h-4 w-4 ${retry.isPending ? "animate-spin" : ""}`} aria-hidden /> Retry {failed} failed
              </button>
            ) : null}
            <button
              type="button"
              className="fp-btn fp-btn-secondary h-9 text-[13px]"
              disabled={busy || healable.length === 0}
              onClick={() => heal.mutate()}
              title={healable.length === 0 ? "Every document already matches its schema" : undefined}
            >
              {heal.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Wand2 className="h-4 w-4" aria-hidden />}
              Heal schema{healable.length ? ` (${healable.length})` : ""}
            </button>
            <button type="button" className="fp-btn fp-btn-primary h-9 text-[13px]" disabled={busy} onClick={() => dispatch.mutate()}>
              {dispatch.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Send className="h-4 w-4" aria-hidden />}
              Dispatch
            </button>
            <button
              type="button"
              className="fp-btn fp-btn-ghost h-9 text-[13px]"
              disabled={busy}
              onClick={() => archive.mutate(archived ? "ACTIVE" : "ARCHIVED")}
            >
              {archived ? <ArchiveRestore className="h-4 w-4" aria-hidden /> : <Archive className="h-4 w-4" aria-hidden />}
              {archived ? "Restore" : "Archive"}
            </button>
            <button type="button" className="fp-btn fp-btn-ghost h-9 text-[13px] text-destructive" onClick={() => setConfirmDelete(true)}>
              <Trash2 className="h-4 w-4" aria-hidden /> Delete
            </button>
          </div>
        ) : null}
      </header>

      <section aria-label="Batch figures" className="grid grid-cols-2 gap-3 xl:grid-cols-5">
        <div className="col-span-2 xl:col-span-1">
        <StatTile
          label="Progress"
          testId="kpi-progress"
          value={`${batch.progress.completed + batch.progress.failed} / ${batch.progress.documents}`}
          hint={
            <span className="block space-y-1.5">
              <SegmentedProgress
                completed={batch.progress.completed}
                failed={batch.progress.failed}
                processing={batch.progress.processing}
                queued={batch.progress.queued}
                label="Batch progress"
              />
              <span className="block">
                {batch.progress.failed ? `${batch.progress.failed} failed · ` : ""}
                {batch.progress.queued + batch.progress.processing ? `${batch.progress.queued + batch.progress.processing} in flight` : "All finished"}
              </span>
            </span>
          }
        />
        </div>
        <StatTile
          label="Straight-through rate"
          testId="kpi-stp"
          value={pct(a?.straight_through_rate)}
          hint={a ? `${a.lanes.straight_through} of ${a.lanes.straight_through + a.lanes.review + a.lanes.exception} finished documents` : undefined}
          icon={RouteIcon}
        />
        <StatTile
          label="Mean confidence"
          value={pct(a?.confidence.mean)}
          hint={a ? `${a.confidence.scored} verified · ${a.confidence.unscored} unscored` : undefined}
          icon={Gauge}
        />
        <StatTile
          label="Match their schema"
          testId="kpi-schema"
          value={a ? `${a.schema.healthy} / ${a.status.completed - a.schema.no_schema}` : "—"}
          hint={a ? `${a.schema.healable} can be healed · ${a.schema.needs_attention} need attention` : undefined}
          icon={Wand2}
        />
        <StatTile
          label="Processing time"
          value={formatSeconds(a?.throughput.median_seconds)}
          hint={a ? `median · p95 ${formatSeconds(a.throughput.p95_seconds)}${a.throughput.documents_per_hour ? ` · ${a.throughput.documents_per_hour}/h` : ""}` : undefined}
          icon={Timer}
        />
      </section>

      <LaneBoard documents={docs} linkTo={linkTo} />

      <section className="grid gap-4 xl:grid-cols-2" aria-label="Confidence analytics">
        <div className="fp-card space-y-3 p-4">
          <div>
            <h2 className="text-[15px] font-semibold tracking-tight">Document confidence</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Verified documents by confidence band. Straight through from {pct(policy.straight_through_min_confidence)}; exceptions below {pct(policy.review_min_confidence)}.
            </p>
          </div>
          {a ? <ConfidenceHistogram buckets={a.confidence.histogram} unit="documents" title="Documents by confidence band" testId="confidence-histogram" /> : <div className="h-44 animate-pulse rounded-lg bg-muted/40" />}
        </div>
        <div className="fp-card space-y-3 p-4">
          <div>
            <h2 className="text-[15px] font-semibold tracking-tight">Field reliability</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">Weakest fields first: mean confidence across verified documents, disagreement and missing required values.</p>
          </div>
          {a && a.fields.length > 0 ? (
            <div className="max-h-72 overflow-auto rounded-lg border border-border">
              <table className="w-full text-left text-xs" data-testid="field-reliability">
                <thead className="sticky top-0 bg-muted/70 text-[10.5px] uppercase tracking-wide text-muted-foreground backdrop-blur">
                  <tr>
                    <th className="px-2.5 py-2 font-semibold">Field</th>
                    <th className="px-2.5 py-2 font-semibold">Mean</th>
                    <th className="px-2.5 py-2 text-right font-semibold">Disagree</th>
                    <th className="px-2.5 py-2 text-right font-semibold">Missing</th>
                  </tr>
                </thead>
                <tbody>
                  {a.fields.map((f) => (
                    <tr key={f.field} className="border-t border-border/60">
                      <td className="max-w-[10rem] truncate px-2.5 py-1.5 font-mono" title={f.field}>{f.field}</td>
                      <td className="px-2.5 py-1.5">
                        {f.mean_confidence === null ? (
                          <span className="text-muted-foreground">—</span>
                        ) : (
                          <span className="flex items-center gap-2">
                            <span className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
                              <span
                                className={`block h-full rounded-full ${f.mean_confidence >= policy.straight_through_min_confidence ? "bg-emerald-500" : f.mean_confidence >= policy.review_min_confidence ? "bg-amber-400" : "bg-destructive"}`}
                                style={{ width: `${f.mean_confidence * 100}%` }}
                              />
                            </span>
                            <span className="fp-num">{pct(f.mean_confidence)}</span>
                          </span>
                        )}
                      </td>
                      <td className="fp-num px-2.5 py-1.5 text-right">{f.disagreement_rate === null ? "—" : pct(f.disagreement_rate)}</td>
                      <td className="fp-num px-2.5 py-1.5 text-right">{f.missing_required || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
              No field scores yet. Turn on verification in Settings → Documents to score every field.
            </p>
          )}
        </div>
      </section>

      <section className="fp-card overflow-hidden" aria-labelledby="schema-title" data-testid="schema-health">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div>
            <h2 id="schema-title" className="text-[15px] font-semibold tracking-tight">Schema health</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Fields the model named its own way are renamed to the schema, values are retyped (money, dates,
              currencies, lists). Missing, unreadable or ambiguous values are reported, never guessed.
            </p>
          </div>
          {canAct && healable.length > 0 ? (
            <button type="button" className="fp-btn fp-btn-primary h-8 text-xs" disabled={busy} onClick={() => heal.mutate()}>
              <Wand2 className="h-3.5 w-3.5" aria-hidden /> Heal {healable.length} document{healable.length === 1 ? "" : "s"}
            </button>
          ) : null}
        </header>
        {healReport && healReport.refused > 0 ? (
          <div className="border-b border-amber-500/30 bg-amber-500/[0.06] px-4 py-2.5 text-xs" role="status">
            <div className="flex items-start justify-between gap-2">
              <p className="font-semibold text-amber-800 dark:text-amber-300">
                {healReport.refused} document{healReport.refused === 1 ? " was" : "s were"} not changed:
              </p>
              <button type="button" aria-label="Dismiss" className="text-muted-foreground hover:text-foreground" onClick={() => setHealReport(null)}>
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <ul className="mt-1 space-y-0.5">
              {healReport.results.filter((r) => r.outcome === "refused").slice(0, 8).map((r) => (
                <li key={r.work_item_id}>
                  {docs.find((d) => d.work_item_id === r.work_item_id)?.original_filename ?? r.work_item_id}: {r.message}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {attention.length === 0 ? (
          <p className="px-4 py-8 text-center text-xs text-muted-foreground">
            Every finished document matches its schema{docs.some((d) => d.schema_state === "NO_SCHEMA") ? " (documents of a type with no schema are not checked)" : ""}.
          </p>
        ) : (
          <ul className="divide-y divide-border/60">
            {attention.map((d) => (
              <li key={d.work_item_id} className="grid gap-2 px-4 py-3 md:grid-cols-[minmax(0,14rem)_minmax(0,1fr)]">
                <div className="min-w-0">
                  <Link to={linkTo(d.work_item_id)} className="block truncate text-[13px] font-medium hover:text-primary" title={d.original_filename}>
                    {d.original_filename}
                  </Link>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <SchemaChip state={d.schema_state} />
                    <span className="text-[11px] text-muted-foreground">
                      {d.schema_label}
                      {d.completeness !== null ? ` · ${pct(d.completeness)} of required fields` : ""}
                    </span>
                  </div>
                </div>
                <ChangeList doc={d} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="fp-card overflow-hidden" aria-labelledby="documents-title">
        <header className="border-b border-border px-4 py-3">
          <h2 id="documents-title" className="text-[15px] font-semibold tracking-tight">Documents ({docs.length})</h2>
        </header>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm" data-testid="batch-documents">
            <thead>
              <tr className="sticky top-0 border-b border-border bg-muted/40 text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground">
                <th className="px-4 py-2.5">Document</th>
                <th className="px-4 py-2.5">Type</th>
                <th className="px-4 py-2.5 text-right">Confidence</th>
                <th className="px-4 py-2.5">Lane</th>
                <th className="px-4 py-2.5">Schema</th>
                {canAct ? <th className="w-10 px-2 py-2.5"><span className="sr-only">Actions</span></th> : null}
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.work_item_id} className="border-b border-border/60 last:border-0 hover:bg-muted/30">
                  <td className="max-w-[16rem] px-4 py-2.5">
                    <Link to={linkTo(d.work_item_id)} className="block truncate font-medium hover:text-primary" title={d.original_filename}>
                      {d.original_filename}
                    </Link>
                    <span className="text-[10.5px] font-semibold uppercase tracking-wide text-muted-foreground">{d.status.toLowerCase()}</span>
                  </td>
                  <td className="px-4 py-2.5 text-[13px]">{d.document_type ?? "—"}</td>
                  <td className="fp-num px-4 py-2.5 text-right text-[13px]">{pct(d.confidence)}</td>
                  <td className="px-4 py-2.5">
                    <LaneChip lane={d.lane} />
                    {d.dispatched_lane && d.dispatched_lane !== d.lane ? (
                      <span className="mt-1 block text-[10.5px] text-muted-foreground">dispatched as {d.dispatched_lane.toLowerCase().replace("_", " ")}</span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2.5"><SchemaChip state={d.schema_state} /></td>
                  {canAct ? (
                    <td className="px-2 py-2.5">
                      <button
                        type="button"
                        aria-label={`Remove ${d.original_filename} from the batch`}
                        disabled={removeDoc.isPending}
                        onClick={() => removeDoc.mutate(d.work_item_id)}
                        className="rounded-md p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <PackagesPanel workspaceId={workspaceId} batchId={batch.id} batchName={batch.name} canCreate={canAct} canDownload={canAct} />

      <ConfirmDialog
        open={confirmDelete}
        title="Delete batch"
        message={`Delete "${batch.name}"? Its grouping and recorded lanes go; its ${batch.progress.documents} documents, their tags and its export packages stay.`}
        confirmText="Delete batch"
        loading={remove.isPending}
        onConfirm={() => remove.mutate()}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
};

export default BatchDetail;
