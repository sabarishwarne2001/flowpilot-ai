/**
 * ARCH46-S2:page-obligation — one obligation: when it is due and exactly how
 * that date was computed (every step: the stated date, the anchor, the
 * period, month-end clamping, the business-day roll), the clause it was read
 * from (page and quote), what counts from it, its next occurrences, and its
 * history -- including each due-soon and overdue alert and whether Flow
 * Builder was told. Contributors complete, waive, reopen, confirm or reject
 * it, reassign it and correct its date.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Check, CheckCircle2, Loader2, Lock, RotateCcw, Trash2, XCircle } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import {
  BUTTON_DESTRUCTIVE, BUTTON_GHOST, BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD_LABEL, HINT, INPUT, PAGE_TITLE, SECTION_TITLE,
  SELECT, SURFACE,
} from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { entityPath, obligationPath, obligationsPath, workItemDetailsPath } from "@/routes/tenantPaths";
import {
  completeObligation, deleteObligation, getObligation, obligationKeys, reopenObligation, reviewObligation,
  updateObligation, waiveObligation,
} from "@/services/api/obligations";
import { errorMessage } from "@/services/api/errors";
import { listWorkspaceMembers } from "@/services/api/workspaces";
import {
  KIND_LABELS, REVIEW_LABELS, ROLLS, ROLL_LABELS, STATE_LABELS, STATE_TONE, dueLabel, formatDay,
  type ObligationDetail as Detail, type ObligationUpdate,
} from "@/types/obligations";
import { formatDateTime } from "@/utils/formatters";

const EVENT_LABELS: Readonly<Record<string, string>> = {
  CREATED: "Created", UPDATED: "Edited", DUE_SOON: "Became due soon", OVERDUE: "Became overdue", REOPENED: "Reopened",
  DONE: "Marked done", WAIVED: "Waived", ADVANCED: "Occurrence done; next one due", RESCHEDULED: "Due date moved",
  CONFIRMED: "Confirmed by a reviewer", REJECTED: "Rejected by a reviewer", SUPERSEDED: "No longer in the document",
};

const ObligationDetail: React.FC = () => {
  const { obligationId = "", orgSlug = "", workspaceSlug = "" } = useParams<{ obligationId: string; orgSlug: string; workspaceSlug: string }>();
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.obligations);
  const role = workspace?.role;
  const canEdit = role === "ADMIN" || role === "OWNER" || role === "CONTRIBUTOR";
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [waiveReason, setWaiveReason] = useState("");
  const [note, setNote] = useState("");
  const [newDate, setNewDate] = useState("");
  const [newRoll, setNewRoll] = useState("");
  const query = useQuery({
    queryKey: obligationKeys.detail(workspaceId, obligationId),
    queryFn: () => getObligation(workspaceId, obligationId),
    enabled: Boolean(workspaceId && obligationId && capability.granted),
  });
  const members = useQuery({ queryKey: ["workspace-members", workspaceId], queryFn: () => listWorkspaceMembers(workspaceId),
    enabled: Boolean(workspaceId && canEdit && capability.granted) });
  const done = (detail: Detail): void => {
    queryClient.setQueryData(obligationKeys.detail(workspaceId, obligationId), detail);
    void queryClient.invalidateQueries({ queryKey: obligationKeys.all(workspaceId) });
  };
  const complete = useMutation({ mutationFn: () => completeObligation(workspaceId, obligationId, note.trim() || undefined),
    onSuccess: (d) => { setNote(""); done(d); } });
  const waive = useMutation({ mutationFn: () => waiveObligation(workspaceId, obligationId, waiveReason.trim()),
    onSuccess: (d) => { setWaiveReason(""); done(d); } });
  const reopen = useMutation({ mutationFn: () => reopenObligation(workspaceId, obligationId), onSuccess: done });
  const review = useMutation({ mutationFn: (verdict: "CONFIRM" | "REJECT") => reviewObligation(workspaceId, obligationId, verdict),
    onSuccess: done });
  const edit = useMutation({ mutationFn: (body: ObligationUpdate) => updateObligation(workspaceId, obligationId, body),
    onSuccess: (d) => { setNewDate(""); done(d); } });
  const remove = useMutation({ mutationFn: () => deleteObligation(workspaceId, obligationId),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: obligationKeys.all(workspaceId) }); navigate(obligationsPath(orgSlug, workspaceSlug)); } });
  const busy = complete.isPending || waive.isPending || reopen.isPending || review.isPending || edit.isPending || remove.isPending;
  const failure = [complete, waive, reopen, review, edit, remove].find((m) => m.isError)?.error;

  if (!capability.granted) {
    return (
      <p className="m-6 flex items-center gap-2 text-sm text-muted-foreground">
        <Lock className="h-4 w-4" aria-hidden /> Obligations are included on the Business and Enterprise plans.
      </p>
    );
  }
  if (query.isLoading) {
    return <Loader2 className="m-6 h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (query.isError || !query.data) {
    return <p className="m-6 text-sm text-destructive">{errorMessage(query.error, "This obligation could not be loaded.")}</p>;
  }
  const d = query.data;
  const o = d.obligation;
  const closed = o.state === "DONE" || o.state === "WAIVED";
  const actionable = canEdit && !o.superseded && o.review !== "REJECTED";
  const owners = (members.data?.items ?? []).filter((m) => m.id !== null && m.status === "ACTIVE");

  return (
    <div className="space-y-4 p-4">
      <Link to={obligationsPath(orgSlug, workspaceSlug)} className={`${BUTTON_GHOST} -ml-2`}>
        <ArrowLeft className="h-4 w-4" aria-hidden /> Obligations
      </Link>
      <header className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className={PAGE_TITLE}>{o.title}</h1>
          <span className={`rounded px-2 py-0.5 text-xs font-semibold ${STATE_TONE[o.state]}`}>{STATE_LABELS[o.state]}</span>
          <span className="rounded bg-muted px-2 py-0.5 text-xs font-semibold">{KIND_LABELS[o.kind]}</span>
          {o.review === "PENDING" || o.review === "REJECTED" ? (
            <span className="rounded bg-amber-500/15 px-2 py-0.5 text-xs font-semibold text-amber-700 dark:text-amber-300">{REVIEW_LABELS[o.review]}</span>
          ) : null}
        </div>
        <p className="text-sm">
          Due <span className="font-semibold">{formatDay(o.due_date)}</span>
          {o.due_date ? <span className="text-muted-foreground"> · {dueLabel(o)}</span> : null}
          {o.recurrence_text ? <span className="text-muted-foreground"> · repeats {o.recurrence_text} (occurrence {o.occurrence})</span> : null}
          <span className="text-muted-foreground"> · reminder {o.lead_days} day(s) before · {d.timezone}</span>
        </p>
        {o.superseded ? <p className="text-sm text-muted-foreground">The document no longer states this obligation (it was reprocessed).</p> : null}
      </header>
      {o.reasons.length > 0 && o.review === "PENDING" ? (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <p className="font-semibold">Why this needs a person</p>
          <ul className="ml-4 list-disc">{o.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      ) : null}
      {failure ? <p className="text-sm text-destructive">{errorMessage(failure, "That did not work.")}</p> : null}
      {actionable ? (
        <section className={`${SURFACE} space-y-3 p-4`} aria-label="Actions">
          {o.review === "PENDING" ? (
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={BUTTON_PRIMARY} disabled={busy || !o.due_date} onClick={() => review.mutate("CONFIRM")}>
                <Check className="h-4 w-4" aria-hidden /> Confirm
              </button>
              <button type="button" className={BUTTON_SECONDARY} disabled={busy} onClick={() => review.mutate("REJECT")}>
                <XCircle className="h-4 w-4" aria-hidden /> Not an obligation
              </button>
              {!o.due_date ? <span className={HINT}>Set the due date below before confirming.</span> : null}
            </div>
          ) : null}
          {closed ? (
            <button type="button" className={BUTTON_SECONDARY} disabled={busy} onClick={() => reopen.mutate()}>
              <RotateCcw className="h-4 w-4" aria-hidden /> Reopen
            </button>
          ) : (
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <input className={INPUT} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)}
                  placeholder="Note (optional): notice sent by courier on 2 Nov" aria-label="Completion note" />
                <button type="button" className={BUTTON_PRIMARY} disabled={busy || !o.due_date} onClick={() => complete.mutate()}>
                  <CheckCircle2 className="h-4 w-4" aria-hidden /> {o.recurrence ? "Done — move to the next occurrence" : "Mark done"}
                </button>
              </div>
              <div className="space-y-1">
                <input className={INPUT} value={waiveReason} maxLength={2000} onChange={(e) => setWaiveReason(e.target.value)}
                  placeholder="Why it no longer applies" aria-label="Waiver reason" />
                <button type="button" className={BUTTON_SECONDARY} disabled={busy || !waiveReason.trim()} onClick={() => waive.mutate()}>
                  Waive
                </button>
              </div>
            </div>
          )}
          <div className="grid gap-3 md:grid-cols-3">
            <label className="space-y-1">
              <span className={FIELD_LABEL}>Owner</span>
              <select className={SELECT} value={o.owner_user_id ?? ""} disabled={busy}
                onChange={(e) => edit.mutate({ owner_user_id: e.target.value || null })}>
                <option value="">Unassigned</option>
                {owners.map((m) => <option key={m.user.id} value={m.user.id}>{m.user.email}</option>)}
              </select>
            </label>
            <label className="space-y-1">
              <span className={FIELD_LABEL}>Correct the due date</span>
              <input type="date" className={INPUT} value={newDate} onChange={(e) => setNewDate(e.target.value)} />
            </label>
            <label className="space-y-1">
              <span className={FIELD_LABEL}>On a weekend or holiday</span>
              <select className={SELECT} value={newRoll || o.business_day_rule} onChange={(e) => setNewRoll(e.target.value)}>
                {ROLLS.map((r) => <option key={r} value={r}>{ROLL_LABELS[r]}</option>)}
              </select>
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BUTTON_SECONDARY} disabled={busy || (!newDate && !newRoll)}
              onClick={() => edit.mutate(newDate
                ? { due_date: newDate, ...(newRoll ? { business_day_rule: newRoll } : {}) }
                : { business_day_rule: newRoll })}>
              Save date
            </button>
            {o.origin === "MANUAL" ? (
              <button type="button" className={`${BUTTON_DESTRUCTIVE} ml-auto`} disabled={busy}
                onClick={() => { if (window.confirm("Delete this obligation?")) { remove.mutate(); } }}>
                <Trash2 className="h-4 w-4" aria-hidden /> Delete
              </button>
            ) : null}
          </div>
        </section>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <section className={`${SURFACE} space-y-2 p-4`} aria-label="How the date was computed">
          <h2 className={SECTION_TITLE}>How the date was computed</h2>
          <ol className="ml-4 list-decimal space-y-0.5 text-sm">
            {d.derivation.map((step) => <li key={step}>{step}</li>)}
          </ol>
          <p className={HINT}>
            Business days on {d.calendar_name ?? "weekends only"} · on a non-business day: {ROLL_LABELS[o.business_day_rule]}
            {d.engine_version ? ` · read by ${d.engine_version}` : ""}
          </p>
          {d.anchor ? (
            <p className="text-sm">Counts from{" "}
              <Link className="underline" to={obligationPath(orgSlug, workspaceSlug, d.anchor.id)}>{d.anchor.title}</Link>
              {" "}({formatDay(d.anchor.due_date)})</p>
          ) : null}
          {d.dependents.length > 0 ? (
            <div className="text-sm">
              <p className="font-semibold">Counting from this</p>
              <ul className="ml-4 list-disc">
                {d.dependents.map((x) => (
                  <li key={x.id}><Link className="underline" to={obligationPath(orgSlug, workspaceSlug, x.id)}>{x.title}</Link> — {formatDay(x.due_date)}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {d.upcoming.length > 0 ? (
            <p className="text-sm">Next occurrences: {d.upcoming.map((u) => formatDay(u.due_date)).join(", ")}</p>
          ) : null}
        </section>
        <section className={`${SURFACE} space-y-2 p-4`} aria-label="Where it comes from">
          <h2 className={SECTION_TITLE}>Where it comes from</h2>
          {o.work_item_id ? (
            <p className="text-sm">
              <Link className="underline" to={workItemDetailsPath(orgSlug, workspaceSlug, o.work_item_id)}>{o.work_item_filename ?? "The document"}</Link>
              {d.clause_number ? `, clause ${d.clause_number}` : ""}
              {d.evidence.length > 0 ? `, page ${d.evidence.map((e) => e.page).filter((p, i, a) => a.indexOf(p) === i).join(", ")}` : ""}
            </p>
          ) : <p className="text-sm text-muted-foreground">Added by hand.</p>}
          {d.quote ? <blockquote className="border-l-2 border-primary/50 pl-3 text-sm italic">{d.quote}</blockquote> : null}
          {o.entity_root_id ? (
            <p className="text-sm">Party:{" "}
              <Link className="underline" to={entityPath(orgSlug, workspaceSlug, o.entity_root_id)}>{o.entity_name ?? o.counterparty_name}</Link>
            </p>
          ) : o.counterparty_name ? <p className="text-sm">Party: {o.counterparty_name}</p> : null}
          {o.amount ? <p className="text-sm">Amount: {o.currency ?? ""} {o.amount}</p> : null}
          <p className={HINT}>Confidence {Math.round(o.confidence * 100)}% · {REVIEW_LABELS[o.review]}</p>
          {d.description ? <p className="whitespace-pre-wrap text-sm">{d.description}</p> : null}
        </section>
      </div>
      <section className={`${SURFACE} space-y-2 p-4`} aria-label="History">
        <h2 className={SECTION_TITLE}>History</h2>
        <ol className="space-y-1 text-sm">
          {d.events.map((e) => (
            <li key={e.id} className="flex flex-wrap gap-2">
              <span className="text-xs tabular-nums text-muted-foreground">{formatDateTime(e.created_at)}</span>
              <span>{EVENT_LABELS[e.kind] ?? e.kind}</span>
              {e.due_date && (e.kind === "DUE_SOON" || e.kind === "OVERDUE") ? <span className={HINT}>for {formatDay(e.due_date)}</span> : null}
              {e.emitted ? <span className="rounded bg-primary/10 px-1.5 text-[10px] font-semibold text-primary">Flow Builder told</span> : null}
              {typeof e.detail.note === "string" && e.detail.note ? <span className={HINT}>“{e.detail.note}”</span> : null}
              {typeof e.detail.reason === "string" && e.detail.reason ? <span className={HINT}>{e.detail.reason}</span> : null}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
};

export default ObligationDetail;
