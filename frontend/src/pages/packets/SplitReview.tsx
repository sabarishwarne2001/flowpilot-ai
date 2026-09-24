/**
 * ARCH43-S2:page-split-review — divide a scanned packet. Every page is shown
 * as a thumbnail (fetched with the session through useAuthorizedBlobUrl, the
 * ARCH-41 hook, never a public URL). A boundary is the first page of a new
 * document: click the gap before a page to add or remove one, or drag a
 * boundary marker to another gap. Nothing is cut until "Approve split".
 */
import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Loader2, Scissors } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, HINT, PAGE_TITLE, SECTION_TITLE, SURFACE } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useAuthorizedBlobUrl } from "@/hooks/useAuthorizedBlobUrl";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { packetSplitsPath } from "@/routes/tenantPaths";
import { caseKeys } from "@/services/api/cases";
import { errorMessage } from "@/services/api/errors";
import { approvePacketSplit, correctPacketSplit, getPacketSplit, rejectPacketSplit } from "@/services/api/packets";
import type { PageScore } from "@/types/packets";

const PageThumb: React.FC<{ readonly workspaceId: string; readonly splitId: string; readonly score: PageScore }> = ({
  workspaceId, splitId, score,
}) => {
  const blob = useAuthorizedBlobUrl(
    `/workspaces/${encodeURIComponent(workspaceId)}/packet-splits/${encodeURIComponent(splitId)}/pages/${score.page}/thumbnail`,
  );
  return (
    <figure className="w-32 shrink-0 space-y-1">
      <div className="flex h-40 items-center justify-center overflow-hidden rounded border border-border bg-white">
        {blob.url ? (
          <img src={blob.url} alt={`Page ${score.page}`} className="max-h-full max-w-full" />
        ) : (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Loading page" />
        )}
      </div>
      <figcaption className="text-center text-[11px] text-muted-foreground">
        p{score.page} · {score.doc_type === "other" ? "—" : score.doc_type.replace(/_/g, " ")}
      </figcaption>
    </figure>
  );
};

const SplitReview: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "", splitId = "" } = useParams<{ orgSlug: string; workspaceSlug: string; splitId: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.caseIntelligence);
  const client = useQueryClient();
  const query = useQuery({
    queryKey: caseKeys.split(workspaceId, splitId),
    queryFn: () => getPacketSplit(workspaceId, splitId),
    enabled: Boolean(workspaceId && splitId && capability.granted),
  });
  const [edited, setEdited] = useState<number[] | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const detail = query.data;
  const original = useMemo(() => (detail ? detail.segments.slice(1).map((s) => s.page_start) : []), [detail]);
  const boundaries = edited ?? original;
  const editable = detail?.split.status === "PROPOSED" || detail?.split.status === "SINGLE";
  const refresh = (): void => {
    setEdited(null);
    void client.invalidateQueries({ queryKey: caseKeys.split(workspaceId, splitId) });
    void client.invalidateQueries({ queryKey: caseKeys.splits(workspaceId) });
  };
  const save = useMutation({ mutationFn: () => correctPacketSplit(workspaceId, splitId, boundaries), onSuccess: refresh });
  const approve = useMutation({ mutationFn: () => approvePacketSplit(workspaceId, splitId, boundaries), onSuccess: refresh });
  const reject = useMutation({ mutationFn: () => rejectPacketSplit(workspaceId, splitId), onSuccess: refresh });
  const busy = save.isPending || approve.isPending || reject.isPending;
  const failure = save.error ?? approve.error ?? reject.error;

  const toggle = (page: number): void => {
    const set = new Set(boundaries);
    if (set.has(page)) {set.delete(page);}
    else {set.add(page);}
    setEdited([...set].sort((a, b) => a - b));
  };
  const move = (from: number, to: number): void => {
    if (from === to || boundaries.includes(to)) {return;}
    setEdited(boundaries.map((b) => (b === from ? to : b)).sort((a, b) => a - b));
  };

  if (!capability.granted) {
    return <p className={`${SURFACE} p-6 text-sm`}>The packet dicer is included on the Business and Enterprise plans.</p>;
  }
  if (query.isLoading) {return <Loader2 className="m-6 h-5 w-5 animate-spin" aria-label="Loading" />;}
  if (query.isError || !detail) {return <p className="text-sm text-destructive">{errorMessage(query.error, "Something went wrong.")}</p>;}

  return (
    <div className="space-y-4 p-4">
      <header className="flex flex-wrap items-center gap-3">
        <Scissors className="h-5 w-5" aria-hidden />
        <h1 className={PAGE_TITLE}>{detail.split.original_filename}</h1>
        <span className="rounded bg-muted px-2 py-0.5 text-xs font-semibold">{detail.split.status}</span>
        <Link to={packetSplitsPath(orgSlug, workspaceSlug)} className="ml-auto text-sm underline">All packets</Link>
      </header>
      <p className={HINT}>
        {detail.split.page_count} pages → {boundaries.length + 1} documents. Click the gap before a page to start (or stop
        starting) a document there, or drag a marker. Least certain boundary:{" "}
        {detail.split.certainty !== null ? `${Math.round(detail.split.certainty * 100)}%` : "—"}.
      </p>
      <section className={`${SURFACE} p-3`} aria-label="Pages">
        <div className="flex flex-wrap items-start gap-1">
          {detail.pages.map((score) => {
            const isBoundary = boundaries.includes(score.page);
            return (
              <React.Fragment key={score.page}>
                {score.page > 1 && (
                  <button
                    type="button"
                    aria-pressed={isBoundary}
                    aria-label={`${isBoundary ? "Remove" : "Add"} a document boundary before page ${score.page}`}
                    disabled={!editable || busy}
                    onClick={() => toggle(score.page)}
                    onDragOver={(e) => { if (dragging !== null) {e.preventDefault();} }}
                    onDrop={(e) => { e.preventDefault(); if (dragging !== null) {move(dragging, score.page);} setDragging(null); }}
                    className={`flex h-40 w-3 items-center justify-center rounded ${isBoundary ? "bg-primary" : "bg-transparent hover:bg-muted"}`}
                    title={`p(boundary) = ${Math.round(score.p * 100)}%`}
                  >
                    {isBoundary && (
                      <span
                        draggable={editable}
                        onDragStart={() => setDragging(score.page)}
                        onDragEnd={() => setDragging(null)}
                        className="block h-10 w-2 cursor-grab rounded bg-primary-foreground/80"
                      />
                    )}
                  </button>
                )}
                <PageThumb workspaceId={workspaceId} splitId={splitId} score={score} />
              </React.Fragment>
            );
          })}
        </div>
      </section>
      <section className={`${SURFACE} space-y-2 p-3`}>
        <h2 className={SECTION_TITLE}>Documents</h2>
        <ol className="list-decimal space-y-1 pl-6 text-sm">
          {[1, ...boundaries].map((start, index, all) => {
            const next = all[index + 1];
            const end = next !== undefined ? next - 1 : detail.split.page_count;
            const segment = detail.segments.find((s) => s.page_start === start);
            return (
              <li key={start}>
                Pages {start}–{end}
                {segment?.document_type ? ` · ${segment.document_type.replace(/_/g, " ")}` : ""}
                {segment?.child_work_item_id ? " · created" : ""}
              </li>
            );
          })}
        </ol>
      </section>
      {failure ? <p className="text-sm text-destructive">{errorMessage(failure, "Something went wrong.")}</p> : null}
      {detail.failure_reason ? <p className="text-sm text-destructive">{detail.failure_reason}</p> : null}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BUTTON_SECONDARY} disabled={!editable || busy || edited === null} onClick={() => save.mutate()}>
          Save boundaries
        </button>
        <button type="button" className={BUTTON_PRIMARY} disabled={!editable || busy || boundaries.length === 0} onClick={() => approve.mutate()}>
          Approve split
        </button>
        <button type="button" className={BUTTON_SECONDARY} disabled={!editable || busy} onClick={() => reject.mutate()}>
          Keep as one document
        </button>
        {busy && <Loader2 className="h-4 w-4 animate-spin" aria-label="Working" />}
      </div>
    </div>
  );
};

export default SplitReview;
