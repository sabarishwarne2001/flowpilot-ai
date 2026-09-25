/**
 * ARCH45-S2:page-corroboration-run — one comparison.
 *
 * Top: status (the page polls while the comparison runs), an "out of date"
 * banner with Compare again when a document was reprocessed since, the PDF /
 * CSV / JSON report, and how far each pair of documents agrees. Middle: the
 * discrepancy matrix — a row per difference, a column per document, sorted by
 * materiality, filterable by layer. Choosing a row opens it: the word-level
 * diff of each version against the baseline the reviewer picks, the decision
 * (confirm / dismiss / reopen, with a note) and, below, the synchronized
 * viewer — one pane per document, each jumped to the page that holds the
 * difference with its evidence outlined. Paging one pane moves the others to
 * the same aligned clause (the anchors the engine stored), so the documents
 * stay side by side even when their pagination differs.
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle, ArrowLeft, CheckCircle2, Download, GitCompare, Link2, Link2Off, Loader2, RotateCw, Trash2, XCircle,
} from "lucide-react";

import { AgreementGrid, DiscrepancyMatrix } from "@/components/corroboration/DiscrepancyMatrix";
import { DocumentPane } from "@/components/corroboration/DocumentPane";
import { WordDiff } from "@/components/corroboration/WordDiff";
import { CAPABILITY } from "@/constants/capabilities";
import {
  BUTTON_DESTRUCTIVE, BUTTON_GHOST, BUTTON_PRIMARY, BUTTON_SECONDARY, HINT, INPUT, PAGE_TITLE, SECTION_TITLE, SELECT, SURFACE,
} from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { corroborationPath, corroborationsPath } from "@/routes/tenantPaths";
import {
  corroborationKeys, decideDiscrepancy, deleteRun, downloadReport, getRun, refreshRun, reviewRun,
} from "@/services/api/corroboration";
import { errorMessage } from "@/services/api/errors";
import { reviewKeys } from "@/services/api/queryKeys";
import {
  KIND_LABELS, LAYERS, LAYER_LABELS, STATUS_LABELS, STATUS_TONE, isPending,
  type Decision, type DiscrepancyRow, type ExportFormat, type Layer, type RunDetail, type RunDocument, type RunVerdict,
} from "@/types/corroboration";
import { formatDateTime } from "@/utils/formatters";

type LayerFilter = Layer | "ALL";

const pageCount = (doc: RunDocument): number => Math.max(doc.pages.length, doc.page_count ?? 0, 1);

/** Where each document shows the selected difference: its first evidence span, else the page its value was read on. */
const pagesFor = (row: DiscrepancyRow, docs: readonly RunDocument[], current: Readonly<Record<string, number>>): Record<string, number> => {
  const next: Record<string, number> = { ...current };
  for (const doc of docs) {
    const span = row.evidence.find((s) => s.work_item_id === doc.work_item_id);
    const page = span?.page ?? row.values[doc.work_item_id]?.page ?? null;
    if (page !== null && page >= 1) {
      next[doc.work_item_id] = Math.min(page, pageCount(doc));
    }
  }
  return next;
};

/** Paging one pane: the others follow the clause aligned with the top of the new page (else move by the same step). */
const follow = (
  detail: RunDetail, docId: string, page: number, current: Readonly<Record<string, number>>,
): Record<string, number> => {
  const previous = current[docId] ?? 1;
  const next: Record<string, number> = { ...current, [docId]: page };
  const anchor = detail.anchors
    .filter((a) => a.members[docId]?.page === page)
    .sort((a, b) => (a.members[docId]?.y ?? 0) - (b.members[docId]?.y ?? 0))[0];
  for (const doc of detail.documents) {
    if (doc.work_item_id === docId) {
      continue;
    }
    const target = anchor?.members[doc.work_item_id]?.page ?? (current[doc.work_item_id] ?? 1) + (page - previous);
    next[doc.work_item_id] = Math.min(Math.max(1, target), pageCount(doc));
  }
  return next;
};

const DecisionBar: React.FC<{
  readonly row: DiscrepancyRow;
  readonly pending: boolean;
  readonly onDecide: (status: Decision, note: string | null) => void;
}> = ({ row, pending, onDecide }) => {
  const [note, setNote] = useState(row.note ?? "");
  const clean = note.trim() ? note.trim() : null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input className={`${INPUT} max-w-sm py-1 text-xs`} value={note} maxLength={2000} placeholder="Note (optional)"
        aria-label="Decision note" onChange={(event) => setNote(event.target.value)} />
      {row.status !== "CONFIRMED" ? (
        <button type="button" className={BUTTON_PRIMARY} disabled={pending} onClick={() => onDecide("CONFIRMED", clean)}>
          <CheckCircle2 className="h-4 w-4" aria-hidden /> Confirm
        </button>
      ) : null}
      {row.status !== "DISMISSED" ? (
        <button type="button" className={BUTTON_SECONDARY} disabled={pending} onClick={() => onDecide("DISMISSED", clean)}>
          <XCircle className="h-4 w-4" aria-hidden /> Dismiss
        </button>
      ) : null}
      {row.status !== "OPEN" ? (
        <button type="button" className={BUTTON_GHOST} disabled={pending} onClick={() => onDecide("OPEN", clean)}>
          Reopen
        </button>
      ) : null}
      {pending ? <Loader2 className="h-4 w-4 animate-spin" aria-label="Saving" /> : null}
    </div>
  );
};

const Versions: React.FC<{
  readonly row: DiscrepancyRow;
  readonly documents: readonly RunDocument[];
  readonly baselineId: string;
}> = ({ row, documents, baselineId }) => {
  const base = row.values[baselineId];
  const textual = row.layer === "CLAUSE" && row.kind === "CLAUSE_MODIFIED" && base?.present;
  return (
    <div className="space-y-2">
      {documents.map((doc) => {
        const value = row.values[doc.work_item_id];
        const isBase = doc.work_item_id === baselineId;
        return (
          <div key={doc.work_item_id} className="rounded-lg border border-border/60 p-2">
            <p className="mb-1 text-[11px] font-semibold text-muted-foreground">
              {doc.position + 1}. {doc.label}{isBase ? " (baseline)" : ""}
              {value?.page ? ` · page ${value.page}` : ""}
            </p>
            {!value || value.participates === false ? (
              <p className={HINT}>Not compared for this document.</p>
            ) : !value.present ? (
              <p className="text-xs font-semibold text-red-700 dark:text-red-300">Absent from this document.</p>
            ) : textual && !isBase && base ? (
              <WordDiff before={base.display} after={value.display} />
            ) : (
              <p className="whitespace-pre-wrap break-words text-xs leading-6">{value.display}</p>
            )}
          </div>
        );
      })}
    </div>
  );
};

const CorroborationRun: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const navigate = useNavigate();
  const client = useQueryClient();
  const { orgSlug = "", workspaceSlug = "", runId = "" } = useParams<{ orgSlug: string; workspaceSlug: string; runId: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.universalCorroborator);
  const canEdit = workspace?.role === "ADMIN" || workspace?.role === "OWNER" || workspace?.role === "CONTRIBUTOR";
  const [layer, setLayer] = useState<LayerFilter>("ALL");
  const [materialOnly, setMaterialOnly] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pages, setPages] = useState<Record<string, number>>({});
  const [synced, setSynced] = useState(true);
  const [baselinePick, setBaselinePick] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<ExportFormat | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: corroborationKeys.detail(workspaceId, runId),
    queryFn: () => getRun(workspaceId, runId),
    enabled: Boolean(workspaceId && runId && capability.granted),
    refetchInterval: (q) => (q.state.data && isPending(q.state.data.run.status) ? 2500 : false),
  });
  const refreshAll = async (): Promise<void> => {
    await Promise.all([
      client.invalidateQueries({ queryKey: corroborationKeys.all(workspaceId) }),
      client.invalidateQueries({ queryKey: reviewKeys.all(workspaceId) }),
    ]);
  };
  const refresh = useMutation({
    mutationFn: () => refreshRun(workspaceId, runId),
    onSuccess: async (result) => {
      await refreshAll();
      if (result.run.id !== runId) {
        navigate(corroborationPath(orgSlug, workspaceSlug, result.run.id));
      }
    },
  });
  const decide = useMutation({
    mutationFn: (args: { id: string; status: Decision; note: string | null }) =>
      decideDiscrepancy(workspaceId, runId, args.id, args.status, args.note),
    onSuccess: refreshAll,
  });
  const review = useMutation({
    mutationFn: (verdict: RunVerdict) => reviewRun(workspaceId, runId, verdict),
    onSuccess: refreshAll,
  });
  const remove = useMutation({
    mutationFn: () => deleteRun(workspaceId, runId),
    onSuccess: async () => {
      await refreshAll();
      navigate(corroborationsPath(orgSlug, workspaceSlug));
    },
  });

  const detail = query.data;
  const documents = useMemo(() => [...(detail?.documents ?? [])].sort((a, b) => a.position - b.position), [detail]);
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const row of detail?.discrepancies ?? []) {
      if (!materialOnly || row.is_material) {
        out[row.layer] = (out[row.layer] ?? 0) + 1;
      }
    }
    return out;
  }, [detail, materialOnly]);
  const rows = useMemo(
    () => (detail?.discrepancies ?? []).filter((r) => (layer === "ALL" || r.layer === layer) && (!materialOnly || r.is_material)),
    [detail, layer, materialOnly],
  );
  const selected = rows.find((r) => r.id === selectedId) ?? detail?.discrepancies.find((r) => r.id === selectedId) ?? null;
  const baselineId = baselinePick ?? documents[0]?.work_item_id ?? "";

  const select = (id: string): void => {
    setSelectedId(id);
    const row = detail?.discrepancies.find((r) => r.id === id);
    if (row && detail) {
      setPages((current) => pagesFor(row, documents, current));
    }
  };
  const turn = (docId: string, page: number): void => {
    if (!detail) {
      return;
    }
    setPages((current) => (synced ? follow(detail, docId, page, current) : { ...current, [docId]: page }));
  };
  const download = async (format: ExportFormat): Promise<void> => {
    setDownloading(format);
    setDownloadError(null);
    try {
      await downloadReport(workspaceId, runId, format, documents[0]?.label ?? "comparison");
    } catch (error) {
      setDownloadError(errorMessage(error, "The download failed."));
    } finally {
      setDownloading(null);
    }
  };

  if (!capability.granted) {
    return <p className={`${SURFACE} m-4 p-6 text-sm`}>The document corroborator is included on the Enterprise plan.</p>;
  }
  if (query.isLoading) {
    return <Loader2 className="m-6 h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (query.isError || !detail) {
    return (
      <div className="space-y-2 p-4">
        <Link to={corroborationsPath(orgSlug, workspaceSlug)} className="inline-flex items-center gap-1 text-sm underline">
          <ArrowLeft className="h-4 w-4" aria-hidden /> Comparisons
        </Link>
        <p className="text-sm text-destructive">{errorMessage(query.error, "This comparison could not be loaded.")}</p>
      </div>
    );
  }

  const run = detail.run;
  const hasResult = run.status === "COMPLETED" || run.status === "STALE";
  const failure = refresh.error ?? decide.error ?? review.error ?? remove.error;
  const layerStats = detail.layers;
  // The encoder the fingerprint names; stats say which one actually aligned the clauses (a fallback is noted).
  const encoderUsed = typeof detail.stats.encoder_used === "string" ? detail.stats.encoder_used : run.encoder;
  const encoderNote = typeof detail.stats.encoder_note === "string" ? detail.stats.encoder_note : null;
  return (
    <div className="space-y-4 p-4">
      <header className="space-y-2">
        <Link to={corroborationsPath(orgSlug, workspaceSlug)} className="inline-flex items-center gap-1 text-sm underline">
          <ArrowLeft className="h-4 w-4" aria-hidden /> Comparisons
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <GitCompare className="h-5 w-5" aria-hidden />
          <h1 className={`${PAGE_TITLE} min-w-0 truncate`}>{documents.map((d) => d.label).join(" · ")}</h1>
          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STATUS_TONE[run.status]}`}>{STATUS_LABELS[run.status]}</span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {hasResult ? (["pdf", "csv", "json"] as const).map((format) => (
              <button key={format} type="button" className={BUTTON_SECONDARY} disabled={downloading !== null}
                onClick={() => void download(format)}>
                {downloading === format ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
                {format.toUpperCase()}
              </button>
            )) : null}
            {canEdit ? (
              <button type="button" className={BUTTON_DESTRUCTIVE} disabled={remove.isPending}
                onClick={() => { if (window.confirm("Delete this comparison and its decisions?")) { remove.mutate(); } }}>
                <Trash2 className="h-4 w-4" aria-hidden /> Delete
              </button>
            ) : null}
          </div>
        </div>
        <p className={HINT}>
          {run.document_count} documents · compared {formatDateTime(run.completed_at ?? run.created_at)} · engine {run.engine_version}
          {" "}· clauses aligned with {encoderUsed}
        </p>
        {encoderNote ? <p className={HINT}>{encoderNote}</p> : null}
      </header>

      {downloadError ? <p className="text-sm text-destructive">{downloadError}</p> : null}
      {failure ? <p className="text-sm text-destructive">{errorMessage(failure, "The request failed.")}</p> : null}

      {isPending(run.status) ? (
        <section className={`${SURFACE} flex items-center gap-2 p-4 text-sm`} aria-live="polite">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {run.status === "QUEUED" ? "Queued — the comparison starts in a moment." : "Comparing the documents…"}
        </section>
      ) : null}
      {run.status === "FAILED" ? (
        <section className={`${SURFACE} space-y-2 p-4`}>
          <p className="flex items-center gap-2 text-sm font-semibold text-destructive">
            <AlertTriangle className="h-4 w-4" aria-hidden /> The comparison failed.
          </p>
          {run.error ? <p className="text-xs text-muted-foreground">{run.error}</p> : null}
          {canEdit ? (
            <button type="button" className={BUTTON_PRIMARY} disabled={refresh.isPending} onClick={() => refresh.mutate()}>
              <RotateCw className="h-4 w-4" aria-hidden /> Try again
            </button>
          ) : null}
        </section>
      ) : null}
      {hasResult && detail.stale ? (
        <section className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm" role="status">
          <AlertTriangle className="h-4 w-4 text-amber-600" aria-hidden />
          <span>
            {documents.some((d) => d.changed_since)
              ? `Out of date: ${documents.filter((d) => d.changed_since).map((d) => d.label).join(", ")} changed since this comparison.`
              : "Out of date: the documents or the comparison engine changed since."}
          </span>
          {canEdit ? (
            <button type="button" className={`${BUTTON_PRIMARY} ml-auto`} disabled={refresh.isPending} onClick={() => refresh.mutate()}>
              {refresh.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RotateCw className="h-4 w-4" aria-hidden />}
              Compare again
            </button>
          ) : null}
        </section>
      ) : null}

      {hasResult ? (
        <>
          <section className="grid gap-3 lg:grid-cols-[1fr_auto]">
            <div className={`${SURFACE} space-y-3 p-4`}>
              <div className="flex flex-wrap gap-4">
                <div>
                  <p className="text-2xl font-bold tabular-nums">{run.material_count}</p>
                  <p className={HINT}>material of {run.discrepancy_count} differences</p>
                </div>
                <div>
                  <p className="text-2xl font-bold tabular-nums">{run.open_material_count}</p>
                  <p className={HINT}>material still open</p>
                </div>
                <div>
                  <p className="text-2xl font-bold tabular-nums">{run.max_materiality.toFixed(2)}</p>
                  <p className={HINT}>highest materiality</p>
                </div>
              </div>
              <ul className="flex flex-wrap gap-2 text-[11px]" aria-label="Layers">
                {LAYERS.map((key) => {
                  const stat = layerStats[key] as { status?: string; reason?: string } | undefined;
                  return (
                    <li key={key} className="rounded-full border border-border px-2 py-0.5" title={stat?.reason ?? ""}>
                      {LAYER_LABELS[key]}: {stat?.status === "RAN" ? `${counts[key] ?? 0} found` : (stat?.status ?? "—").toLowerCase().replace(/_/g, " ")}
                    </li>
                  );
                })}
              </ul>
              {detail.rules.length > 0 ? (
                <div>
                  <p className="text-xs font-semibold">Rules applied</p>
                  <ul className="mt-1 space-y-0.5 text-xs">
                    {detail.rules.map((r) => (
                      <li key={r.key}>
                        {r.sentence} <span className="text-muted-foreground">— read as {r.understood_as}{r.source === "WORKSPACE" ? " (workspace)" : ""}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {canEdit && run.open_material_count > 0 ? (
                <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-3">
                  <span className="text-xs text-muted-foreground">All {run.open_material_count} open material differences:</span>
                  <button type="button" className={BUTTON_SECONDARY} disabled={review.isPending} onClick={() => review.mutate("CONFIRM")}>
                    <CheckCircle2 className="h-4 w-4" aria-hidden /> Confirm
                  </button>
                  <button type="button" className={BUTTON_SECONDARY} disabled={review.isPending} onClick={() => review.mutate("DISMISS")}>
                    <XCircle className="h-4 w-4" aria-hidden /> Dismiss
                  </button>
                  {review.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                </div>
              ) : null}
            </div>
            <div className={`${SURFACE} p-4`}>
              <p className="mb-2 text-xs font-semibold">Agreement</p>
              <AgreementGrid documents={documents} pairs={detail.pairs} />
            </div>
          </section>

          <section className={`${SURFACE} space-y-2 p-2`} aria-labelledby="matrix-title">
            <div className="flex flex-wrap items-center gap-2 px-2 pt-1">
              <h2 id="matrix-title" className={SECTION_TITLE}>Discrepancy matrix</h2>
              <div className="flex flex-wrap gap-1" role="tablist" aria-label="Filter by layer">
                {(["ALL", ...LAYERS] as const).map((key) => (
                  <button key={key} type="button" role="tab" aria-selected={layer === key} onClick={() => setLayer(key)}
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${layer === key ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted"}`}>
                    {key === "ALL" ? "All" : LAYER_LABELS[key]}
                    {key !== "ALL" ? ` (${counts[key] ?? 0})` : ""}
                  </button>
                ))}
              </div>
              <label className="ml-auto flex items-center gap-1 text-xs">
                <input type="checkbox" checked={materialOnly} onChange={(event) => setMaterialOnly(event.target.checked)} />
                Material only
              </label>
            </div>
            <DiscrepancyMatrix rows={rows} documents={documents} selectedId={selectedId} onSelect={select} />
          </section>

          {selected ? (
            <section className={`${SURFACE} space-y-3 p-4`} aria-labelledby="difference-title">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="difference-title" className={SECTION_TITLE}>{selected.label}</h2>
                <span className="text-xs text-muted-foreground">
                  {LAYER_LABELS[selected.layer]} · {KIND_LABELS[selected.kind]} · materiality {selected.materiality.toFixed(2)}
                </span>
                <label className="ml-auto flex items-center gap-1 text-xs">
                  Diff against
                  <select className={`${SELECT} w-auto py-1 text-xs`} value={baselineId} onChange={(event) => setBaselinePick(event.target.value)}>
                    {documents.map((d) => <option key={d.work_item_id} value={d.work_item_id}>{d.position + 1}. {d.label}</option>)}
                  </select>
                </label>
              </div>
              <p className="text-sm">{selected.summary}</p>
              <Versions row={selected} documents={documents} baselineId={baselineId} />
              {canEdit ? (
                <DecisionBar key={`${selected.id}:${selected.status}`} row={selected} pending={decide.isPending}
                  onDecide={(status, note) => decide.mutate({ id: selected.id, status, note })} />
              ) : null}
            </section>
          ) : (
            <p className={HINT}>Choose a difference to see each version side by side.</p>
          )}

          <section className="space-y-2" aria-labelledby="viewer-title">
            <div className="flex items-center gap-2">
              <h2 id="viewer-title" className={SECTION_TITLE}>Documents side by side</h2>
              <button type="button" className={`${BUTTON_GHOST} ml-auto text-xs`} aria-pressed={synced} onClick={() => setSynced((s) => !s)}>
                {synced ? <Link2 className="h-4 w-4" aria-hidden /> : <Link2Off className="h-4 w-4" aria-hidden />}
                {synced ? "Pages move together" : "Pages move independently"}
              </button>
            </div>
            <div className="flex gap-2 overflow-x-auto pb-2">
              {documents.map((doc) => {
                const value = selected?.values[doc.work_item_id];
                return (
                  <div key={doc.work_item_id} className="flex min-w-[18rem] flex-1">
                    <DocumentPane
                      workspaceId={workspaceId}
                      runId={runId}
                      document={doc}
                      page={pages[doc.work_item_id] ?? 1}
                      onPage={(page) => turn(doc.work_item_id, page)}
                      spans={selected ? selected.evidence.filter((s) => s.work_item_id === doc.work_item_id) : []}
                      severity={selected?.severity ?? "LOW"}
                      fallbackText={value ? (value.present ? value.display : "Absent from this document.") : null}
                      baseline={doc.work_item_id === baselineId}
                    />
                  </div>
                );
              })}
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
};

export default CorroborationRun;
