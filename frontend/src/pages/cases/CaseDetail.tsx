/**
 * ARCH43-S2:page-case-detail — one case: the checklist of required documents
 * (with a single-use upload link for what is missing), the documents, and the
 * consistency matrix (every rule, its outcome and the two values compared).
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { CheckCircle2, CircleDashed, Loader2 } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, HINT, INPUT, PAGE_TITLE, SCROLL_X, SECTION_TITLE, SURFACE, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { casesPath } from "@/routes/tenantPaths";
import {
  addCaseDocument, caseKeys, closeCase, evaluateCase, getCase, removeCaseDocument, requestDocument, revokeDocumentRequest,
} from "@/services/api/cases";
import { errorMessage } from "@/services/api/errors";
import { CASE_STATUS_LABELS, type RuleOutcome } from "@/types/cases";
// ARCH46-S2:h8-timestamps — instants follow the reader's profile zone and language (ARCH-30 D-5; verify_arch30_tranche3 H8).
import { formatTimestamp } from "@/utils/displayTime";

const OUTCOME_STYLE: Readonly<Record<RuleOutcome, string>> = {
  PASS: "bg-green-100 text-green-800", FAIL: "bg-red-100 text-red-800", MISSING: "bg-muted text-muted-foreground", ERROR: "bg-amber-100 text-amber-800",
};

const CaseDetailPage: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "", caseId = "" } = useParams<{ orgSlug: string; workspaceSlug: string; caseId: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.caseIntelligence);
  const client = useQueryClient();
  const key = caseKeys.detail(workspaceId, caseId);
  const query = useQuery({ queryKey: key, queryFn: () => getCase(workspaceId, caseId), enabled: Boolean(workspaceId && caseId && capability.granted) });
  const [link, setLink] = useState<string | null>(null);
  const [addId, setAddId] = useState("");
  const done = (): void => { void client.invalidateQueries({ queryKey: key }); void client.invalidateQueries({ queryKey: caseKeys.all(workspaceId) }); };
  const evaluate = useMutation({ mutationFn: () => evaluateCase(workspaceId, caseId), onSuccess: done });
  const close = useMutation({ mutationFn: () => closeCase(workspaceId, caseId), onSuccess: done });
  const add = useMutation({ mutationFn: () => addCaseDocument(workspaceId, caseId, addId.trim()), onSuccess: () => { setAddId(""); done(); } });
  const remove = useMutation({ mutationFn: (id: string) => removeCaseDocument(workspaceId, caseId, id), onSuccess: done });
  const request = useMutation({
    mutationFn: (docType: string) => requestDocument(workspaceId, caseId, docType, ""),
    onSuccess: (created) => { setLink(`${window.location.origin}${created.upload_path}`); done(); },
  });
  const revoke = useMutation({ mutationFn: (id: string) => revokeDocumentRequest(workspaceId, caseId, id), onSuccess: done });
  const failure = evaluate.error ?? close.error ?? add.error ?? remove.error ?? request.error ?? revoke.error;

  if (!capability.granted) {return <p className={`${SURFACE} m-4 p-6 text-sm`}>Case intelligence is included on the Business and Enterprise plans.</p>;}
  if (query.isLoading) {return <Loader2 className="m-6 h-5 w-5 animate-spin" aria-label="Loading" />;}
  if (query.isError || !query.data) {return <p className="m-4 text-sm text-destructive">{errorMessage(query.error, "The case could not be loaded.")}</p>;}
  const { case: c, checklist, documents, rules, requests, template } = query.data;
  const closed = c.status === "CLOSED";
  return (
    <div className="space-y-4 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className={PAGE_TITLE}>{c.title}</h1>
        <span className="rounded bg-muted px-2 py-0.5 text-xs font-semibold">{CASE_STATUS_LABELS[c.status]}</span>
        <span className={HINT}>{template.name} v{template.version} · {Math.round(c.completeness * 100)}% complete</span>
        <Link to={casesPath(orgSlug, workspaceSlug)} className="ml-auto text-sm underline">All cases</Link>
      </header>
      {failure ? <p className="text-sm text-destructive">{errorMessage(failure, "The request failed.")}</p> : null}
      {link ? (
        <p className={`${SURFACE} break-all p-3 text-sm`}>
          Send this link to the person who has the document. It works once and is shown only now: <code>{link}</code>
        </p>
      ) : null}
      <section className={`${SURFACE} space-y-2 p-4`} aria-label="Checklist">
        <h2 className={SECTION_TITLE}>Checklist</h2>
        <ul className="space-y-1 text-sm">
          {checklist.map((slot) => (
            <li key={slot.doc_type} className="flex flex-wrap items-center gap-2">
              {slot.satisfied ? <CheckCircle2 className="h-4 w-4 text-green-600" aria-label="present" /> : <CircleDashed className="h-4 w-4" aria-label="missing" />}
              <span>{slot.label}</span>
              <span className={HINT}>{slot.present}/{slot.min_count}</span>
              {!slot.satisfied && !closed && (
                <button type="button" className={BUTTON_SECONDARY} disabled={request.isPending} onClick={() => request.mutate(slot.doc_type)}>
                  Request upload link
                </button>
              )}
            </li>
          ))}
        </ul>
      </section>
      <section className={`${SURFACE} ${SCROLL_X} p-4`} aria-label="Consistency matrix">
        <h2 className={SECTION_TITLE}>Consistency</h2>
        <table className="mt-2 w-full text-sm">
          <thead><tr className={TABLE_HEAD}><th className="p-2 text-left">Rule</th><th className="p-2">Check</th><th className="p-2">Result</th><th className="p-2 text-left">Left</th><th className="p-2 text-left">Right</th></tr></thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.rule_id} className={TABLE_ROW}>
                <td className="p-2">{r.label}</td>
                <td className="p-2 text-center font-mono text-xs">{r.op}</td>
                <td className="p-2 text-center"><span className={`rounded px-2 py-0.5 text-xs font-bold ${OUTCOME_STYLE[r.outcome]}`}>{r.outcome}</span></td>
                <td className="p-2">{r.left_value ?? "—"}</td>
                <td className="p-2">{r.right_value ?? "—"}</td>
              </tr>
            ))}
            {rules.length === 0 ? <tr><td colSpan={5} className="p-3 text-center text-muted-foreground">This template has no rules.</td></tr> : null}
          </tbody>
        </table>
      </section>
      <section className={`${SURFACE} space-y-2 p-4`} aria-label="Documents">
        <h2 className={SECTION_TITLE}>Documents</h2>
        <ul className="space-y-1 text-sm">
          {documents.map((d) => (
            <li key={d.work_item_id} className="flex flex-wrap items-center gap-2">
              <span>{d.original_filename}</span><span className={HINT}>{d.document_type.replace(/_/g, " ")} · {d.source.toLowerCase()}</span>
              {!closed && <button type="button" className="text-xs underline" onClick={() => remove.mutate(d.work_item_id)}>Remove</button>}
            </li>
          ))}
        </ul>
        {!closed && (
          <div className="flex gap-2">
            <input className={INPUT} aria-label="Document id" placeholder="Document id" value={addId} onChange={(e) => setAddId(e.target.value)} />
            <button type="button" className={BUTTON_SECONDARY} disabled={!addId.trim() || add.isPending} onClick={() => add.mutate()}>Add document</button>
          </div>
        )}
      </section>
      {requests.length > 0 && (
        <section className={`${SURFACE} space-y-1 p-4 text-sm`} aria-label="Requests">
          <h2 className={SECTION_TITLE}>Upload requests</h2>
          {requests.map((r) => (
            <p key={r.id} className="flex flex-wrap items-center gap-2">
              {r.document_type.replace(/_/g, " ")} · {r.status.toLowerCase()} · expires {formatTimestamp(r.expires_at)}
              {r.status === "OPEN" && <button type="button" className="text-xs underline" onClick={() => revoke.mutate(r.id)}>Withdraw</button>}
            </p>
          ))}
        </section>
      )}
      {!closed && (
        <div className="flex gap-2">
          <button type="button" className={BUTTON_SECONDARY} disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>Re-check</button>
          <button type="button" className={BUTTON_PRIMARY} disabled={close.isPending} onClick={() => close.mutate()}>Close case</button>
        </div>
      )}
    </div>
  );
};

export default CaseDetailPage;
