/**
 * ARCH47-S2:page-erp-posting — one posting: what was built (canonical object,
 * mapped record, rendered file), every attempt the ledger made (render, send,
 * probe, acknowledgement, review), the ERP's acknowledgement and reference,
 * and the decisions a person can take: retry, accept (the ERP has it), cancel,
 * or — for a file imported by hand — confirm the import or paste the ERP's
 * response file.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, BookUp, Download, Loader2 } from "lucide-react";

import { ErpLocked, JsonBlock, PostingStateBadge, canContribute, money, sourcePath } from "@/components/erp/common";
import { CAPABILITY } from "@/constants/capabilities";
import {
  BUTTON_DESTRUCTIVE, BUTTON_GHOST, BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD_LABEL, HINT, INPUT, PAGE_TITLE, SECTION_TITLE,
  SURFACE, TEXTAREA,
} from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { erpPath, erpTargetPath, workItemDetailsPath } from "@/routes/tenantPaths";
import {
  acceptPosting, acknowledgePosting, cancelPosting, downloadPostingFile, erpKeys, getPosting, retryPosting,
} from "@/services/api/erp";
import { errorMessage } from "@/services/api/errors";
import { OBJECT_LABELS, SOURCE_LABELS, verdictsFor, type AcknowledgeRequest, type PostingDetail as Detail, type PostingVerdict } from "@/types/erp";
import { formatTimestamp } from "@/utils/displayTime";

type View = "file" | "mapped" | "canonical" | "ack";

const OUTCOME_TONE: Readonly<Record<string, string>> = {
  OK: "text-emerald-700 dark:text-emerald-400", ACCEPTED: "text-emerald-700 dark:text-emerald-400",
  FOUND: "text-emerald-700 dark:text-emerald-400", TRANSIENT: "text-amber-700 dark:text-amber-400",
  PENDING: "text-muted-foreground", ABSENT: "text-muted-foreground", UNCERTAIN: "text-fuchsia-700 dark:text-fuchsia-400",
  PERMANENT: "text-destructive", REJECTED: "text-destructive", MISMATCH: "text-orange-700 dark:text-orange-400",
};

const VERDICT_LABELS: Readonly<Record<PostingVerdict, string>> = {
  RETRY: "Retry", ACCEPT: "The ERP has it", CANCEL: "Do not post",
};

const ErpPostingDetail: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "", postingId = "" } = useParams<{ orgSlug: string; workspaceSlug: string; postingId: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.erpPosting);
  const canAct = canContribute(workspace?.role);
  const queryClient = useQueryClient();
  const [view, setView] = useState<View>("file");
  const [note, setNote] = useState("");
  const [reference, setReference] = useState("");
  const [response, setResponse] = useState("");
  const [reason, setReason] = useState("");
  const query = useQuery({
    queryKey: erpKeys.posting(workspaceId, postingId),
    queryFn: () => getPosting(workspaceId, postingId),
    enabled: Boolean(workspaceId && postingId && capability.granted),
    refetchInterval: (q) => {
      const s = q.state.data?.posting.state;
      return s === "PENDING" || s === "SENDING" || s === "RETRYING" ? 5_000 : false;
    },
  });
  const settle = (data: Detail): void => {
    queryClient.setQueryData(erpKeys.posting(workspaceId, postingId), data);
    void queryClient.invalidateQueries({ queryKey: erpKeys.all(workspaceId) });
    setNote(""); setReference(""); setResponse(""); setReason("");
  };
  const decide = useMutation({
    mutationFn: (verdict: PostingVerdict) => {
      const body = { note: note.trim() || null, reference: reference.trim() || null };
      if (verdict === "RETRY") {
        return retryPosting(workspaceId, postingId, body);
      }
      if (verdict === "ACCEPT") {
        return acceptPosting(workspaceId, postingId, body);
      }
      return cancelPosting(workspaceId, postingId, body);
    },
    onSuccess: settle,
  });
  const acknowledge = useMutation({
    mutationFn: (body: AcknowledgeRequest) => acknowledgePosting(workspaceId, postingId, body),
    onSuccess: settle,
  });
  const download = useMutation({
    mutationFn: (filename: string) => downloadPostingFile(workspaceId, postingId, filename),
  });

  if (!capability.granted) {
    return <ErpLocked />;
  }
  if (query.isLoading) {
    return <div className="p-4"><Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /></div>;
  }
  if (query.isError || !query.data) {
    return <p className="p-4 text-sm text-destructive">{errorMessage(query.error, "Could not load the posting.")}</p>;
  }
  const d = query.data;
  const p = d.posting;
  const verdicts = verdictsFor(p.state);
  const manualAck = p.state === "DELIVERED";
  const readFile = (file: File | undefined): void => {
    if (!file) {
      return;
    }
    const reader = new FileReader();
    reader.onload = () => setResponse(typeof reader.result === "string" ? reader.result : "");
    reader.readAsText(file);
  };

  return (
    <div className="space-y-4 p-4">
      <Link to={erpPath(orgSlug, workspaceSlug)} className={BUTTON_GHOST}><ArrowLeft className="h-4 w-4" aria-hidden /> ERP posting</Link>
      <header className="flex flex-wrap items-center gap-2">
        <BookUp className="h-5 w-5" aria-hidden />
        <h1 className={PAGE_TITLE}>{OBJECT_LABELS[p.object_kind]} {p.document_number ?? ""}</h1>
        <PostingStateBadge state={p.state} />
        <span className={HINT}>to <Link className="hover:underline" to={erpTargetPath(orgSlug, workspaceSlug, p.target_id)}>{p.target_name}</Link></span>
        {canAct && p.rendered_filename && !p.erased ? (
          <button type="button" className={`${BUTTON_SECONDARY} ml-auto`} disabled={download.isPending}
            onClick={() => download.mutate(p.rendered_filename ?? "posting")}>
            {download.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
            {p.rendered_filename}
          </button>
        ) : null}
      </header>
      {download.isError ? <p className="text-sm text-destructive">{errorMessage(download.error, "Could not download.")}</p> : null}

      <section className={`${SURFACE} grid gap-3 p-4 text-sm md:grid-cols-4`} aria-label="Summary">
        <div><p className={HINT}>Amount</p><p className="tabular-nums">{money(p.amount, p.currency)}</p></div>
        <div><p className={HINT}>ERP reference</p><p className="font-mono text-xs">{p.external_id ?? "—"}</p></div>
        <div><p className={HINT}>Attempts</p><p>{p.attempts} of {p.max_attempts}{p.next_attempt_at ? ` · next ${formatTimestamp(p.next_attempt_at)}` : ""}</p></div>
        <div><p className={HINT}>Mapping</p><p>{p.mapping_version ? `version ${p.mapping_version}` : "—"}</p></div>
        <div>
          <p className={HINT}>From</p>
          <p>
            <Link className="hover:underline" to={sourcePath(orgSlug, workspaceSlug, p.source_kind, p.source_id)}>{d.source_label ?? SOURCE_LABELS[p.source_kind]}</Link>
            {p.work_item_id ? <> · <Link className="hover:underline" to={workItemDetailsPath(orgSlug, workspaceSlug, p.work_item_id)}>{p.work_item_filename ?? "document"}</Link></> : null}
          </p>
        </div>
        <div><p className={HINT}>Started by</p><p>{p.origin === "MANUAL" ? "A person" : p.origin === "AUTO" ? "Auto-post" : "A Flow Builder rule"}</p></div>
        <div><p className={HINT}>Delivered</p><p>{formatTimestamp(p.delivered_at)}</p></div>
        <div><p className={HINT}>Acknowledged</p><p>{formatTimestamp(p.acknowledged_at)}</p></div>
        <div className="md:col-span-4"><p className={HINT}>Idempotency key</p><p className="break-all font-mono text-[11px]">{d.idempotency_key}</p></div>
        {d.remote_path ? <div className="md:col-span-4"><p className={HINT}>Remote file</p><p className="break-all font-mono text-[11px]">{d.remote_path}</p></div> : null}
      </section>

      {p.last_error && p.state !== "DONE" ? (
        <p className="rounded-lg border border-destructive/40 bg-destructive/10 p-2 text-sm">{p.last_error}</p>
      ) : null}
      {d.source_changed ? (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-sm">
          The approved outcome changed after this posting was built. A retry re-sends what was approved then; cancel it and
          post again to send the new figures.
        </p>
      ) : null}
      {p.erased ? <p className={HINT}>The content of this posting was erased under a data-subject request; its ledger entry remains.</p> : null}
      {d.review_note ? <p className={HINT}>Reviewer&apos;s note: {d.review_note}</p> : null}

      {canAct && (verdicts.length > 0 || manualAck) ? (
        <section className={`${SURFACE} space-y-3 p-4`} aria-labelledby="posting-decide">
          <h2 id="posting-decide" className={SECTION_TITLE}>Decide</h2>
          {p.state === "UNCERTAIN" ? (
            <p className="text-sm">
              FlowPilot cannot tell whether the ERP received this, and cannot check. Look in the ERP: retry only if it does not
              have the document; if it does, mark it posted with the ERP&apos;s reference.
            </p>
          ) : null}
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1">
              <span className={FIELD_LABEL}>ERP reference (when it has it)</span>
              <input className={INPUT} value={reference} maxLength={200} onChange={(e) => setReference(e.target.value)} placeholder="Bill 1043 / doc id" />
            </label>
            <label className="space-y-1">
              <span className={FIELD_LABEL}>Note</span>
              <input className={INPUT} value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            {verdicts.map((v) => (
              <button key={v} type="button" disabled={decide.isPending}
                className={v === "CANCEL" ? BUTTON_DESTRUCTIVE : v === "RETRY" ? BUTTON_PRIMARY : BUTTON_SECONDARY}
                onClick={() => { if (v !== "CANCEL" || window.confirm("This posting will not be sent. Continue?")) { decide.mutate(v); } }}>
                {VERDICT_LABELS[v]}
              </button>
            ))}
            {decide.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
          </div>
          {decide.isError ? <p className="text-sm text-destructive">{errorMessage(decide.error, "That decision was refused.")}</p> : null}
          {manualAck ? (
            <div className="space-y-2 border-t border-border/60 pt-3">
              <p className="text-sm">
                Imported the file into the ERP? Confirm with the ERP&apos;s reference, report that it was refused, or paste the
                ERP&apos;s response file (a Tally import response, an X12 997, an .ack file) and FlowPilot reads it.
              </p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={BUTTON_PRIMARY} disabled={acknowledge.isPending}
                  onClick={() => acknowledge.mutate({ accepted: true, reference: reference.trim() || null })}>Imported</button>
                <input className={`${INPUT} w-64`} value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="Why the ERP refused it" aria-label="Refusal reason" />
                <button type="button" className={BUTTON_DESTRUCTIVE} disabled={acknowledge.isPending || !reason.trim()}
                  onClick={() => acknowledge.mutate({ accepted: false, reason: reason.trim() })}>Refused</button>
              </div>
              <textarea className={`${TEXTAREA} font-mono text-xs`} value={response} onChange={(e) => setResponse(e.target.value)}
                placeholder="Paste the ERP's response file here" aria-label="Response file" spellCheck={false} />
              <div className="flex flex-wrap items-center gap-2">
                <input type="file" accept=".xml,.txt,.ack,.997,.edi,.x12" aria-label="Choose a response file" onChange={(e) => readFile(e.target.files?.[0])} />
                <button type="button" className={BUTTON_SECONDARY} disabled={acknowledge.isPending || !response.trim()}
                  onClick={() => acknowledge.mutate({ response_text: response })}>Read the response</button>
              </div>
              {acknowledge.isError ? <p className="text-sm text-destructive">{errorMessage(acknowledge.error, "The acknowledgement was refused.")}</p> : null}
            </div>
          ) : null}
        </section>
      ) : null}

      <section className={`${SURFACE} space-y-2 p-4`} aria-label="Content">
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="Posting content">
          {([["file", "File"], ["mapped", "Mapped"], ["canonical", "Canonical"], ["ack", "Acknowledgement"]] as const).map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={view === id} onClick={() => setView(id)}
              className={`rounded-md px-2 py-1 text-xs font-semibold ${view === id ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted"}`}>
              {label}
            </button>
          ))}
          {d.rendered_media_type ? <span className={`${HINT} ml-auto`}>{d.rendered_media_type}{d.rendered_size ? ` · ${d.rendered_size} bytes` : ""}</span> : null}
        </div>
        {view === "file" ? (d.rendered_preview ? (
          <pre aria-label="Rendered file" className="max-h-[28rem] overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/50 p-3 font-mono text-[11px]">{d.rendered_preview}</pre>
        ) : <p className={HINT}>{p.rendered_filename ? "A binary file (download it to open)." : "Not rendered."}</p>) : null}
        {view === "mapped" ? (d.mapped ? <JsonBlock label="Mapped record" value={d.mapped} /> : <p className={HINT}>Not mapped.</p>) : null}
        {view === "canonical" ? (d.canonical ? <JsonBlock label="Canonical object" value={d.canonical} /> : <p className={HINT}>Not built.</p>) : null}
        {view === "ack" ? <JsonBlock label="Acknowledgement" value={{ ack: d.ack, control_numbers: d.control_numbers }} /> : null}
      </section>

      <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="posting-attempts">
        <h2 id="posting-attempts" className={SECTION_TITLE}>Attempts</h2>
        {d.attempts.length === 0 ? <p className={HINT}>None yet.</p> : (
          <ol className="space-y-2">
            {d.attempts.map((a) => (
              <li key={a.seq} className="border-l-2 border-border pl-3 text-sm">
                <p>
                  <span className="font-mono text-xs text-muted-foreground">#{a.seq}</span>{" "}
                  <span className="font-semibold">{a.kind}</span>{" "}
                  <span className={`font-semibold ${OUTCOME_TONE[a.outcome] ?? ""}`}>{a.outcome}</span>
                  {a.http_status ? <span className="text-xs text-muted-foreground"> · HTTP {a.http_status}</span> : null}
                  <span className="text-xs text-muted-foreground"> · {formatTimestamp(a.started_at)}</span>
                </p>
                {a.message ? <p className="text-xs">{a.message}</p> : null}
                {Object.keys(a.detail).length > 0 ? (
                  <details><summary className="cursor-pointer text-xs text-muted-foreground">Detail</summary><JsonBlock label={`Attempt ${a.seq}`} value={a.detail} /></details>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
};

export default ErpPostingDetail;
