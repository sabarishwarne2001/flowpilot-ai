/**
 * ARCH49-S2:proposal-panel — one of the exception agent's proposals: what it
 * would do and why (server-rendered rationale from numbers, never document
 * text), why it waits for a person, every tool it called in order, and what
 * the source says — each excerpt a FENCED quote of untrusted data, rendered as
 * text (never HTML, never instructions), with the injection score beside it.
 *
 * Approve applies it through the owning service with the version the agent
 * read, so it meets exactly what the review hub meets: a decision someone made
 * first (the proposal is overtaken) or a person's lock (it waits; nothing ever
 * breaks the lock). Reject takes a reason from a closed list. Undo cancels a
 * scheduled automatic resolution before it takes effect.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Bot, Check, Clock, Loader2, Quote, Undo2, X } from "lucide-react";

import { ProposalStatusBadge, actionError, pct } from "@/components/process/common";
import { BUTTON_GHOST, BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD_LABEL, HINT, SECTION_TITLE, SELECT, SURFACE_INSET } from "@/components/ui/primitives";
import { reviewKeys } from "@/services/api/queryKeys";
import { approveProposal, getProposal, processKeys, rejectProposal, undoProposal } from "@/services/api/process";
import { errorMessage } from "@/services/api/errors";
import { formatTimestamp } from "@/utils/displayTime";
import {
  HOLD_LABELS, LIVE_STATUSES, REJECT_REASONS, REJECT_REASON_LABELS, TOOL_LABELS, type ProposalRow, type RejectReason,
} from "@/types/process";

export const HoldList: React.FC<{ readonly holds: readonly string[] }> = ({ holds }) =>
  holds.length ? (
    <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
      {holds.map((h) => <li key={h}>{HOLD_LABELS[h] ?? h}</li>)}
    </ul>
  ) : null;

export const ProposalSummary: React.FC<{ readonly proposal: ProposalRow }> = ({ proposal }) => (
  <div className="space-y-2">
    <div className="flex flex-wrap items-center gap-2">
      <Bot className="h-4 w-4 text-primary" aria-hidden />
      <span className="text-sm font-semibold">{proposal.label}</span>
      {proposal.verdict ? <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-bold">{proposal.verdict}</span> : null}
      <ProposalStatusBadge status={proposal.status} />
      <span className={HINT}>
        confidence {pct(proposal.confidence)}
        {proposal.calibrated_probability !== null ? ` · calibrated ${pct(proposal.calibrated_probability)}` : ""}
      </span>
      {proposal.injection_suspected ? (
        <span className="inline-flex items-center gap-1 rounded bg-destructive/10 px-1.5 py-0.5 text-[11px] font-semibold text-destructive">
          <AlertTriangle className="h-3 w-3" aria-hidden /> A source reads like instructions to an AI
        </span>
      ) : null}
    </div>
    {proposal.rationale.length ? (
      <ul className="list-disc space-y-0.5 pl-5 text-sm">
        {proposal.rationale.map((line) => <li key={line}>{line}</li>)}
      </ul>
    ) : null}
    {proposal.status === "AUTO_SCHEDULED" && proposal.apply_after ? (
      <p className="flex items-center gap-1 text-xs text-amber-800 dark:text-amber-300">
        <Clock className="h-3.5 w-3.5" aria-hidden /> Applies itself at {formatTimestamp(proposal.apply_after)} unless someone undoes it.
      </p>
    ) : null}
    {proposal.waiting_until && LIVE_STATUSES.includes(proposal.status) ? (
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        <Clock className="h-3.5 w-3.5" aria-hidden /> Someone is deciding this item; it waits until {formatTimestamp(proposal.waiting_until)}.
      </p>
    ) : null}
    {proposal.status === "PROPOSED" && proposal.holds.length ? (
      <div>
        <p className={FIELD_LABEL}>Why a person decides</p>
        <HoldList holds={proposal.holds} />
      </div>
    ) : null}
    {proposal.failure ? <p className="text-xs text-muted-foreground">{proposal.failure}</p> : null}
    {proposal.reject_reason ? <p className="text-xs text-muted-foreground">Rejected: {REJECT_REASON_LABELS[proposal.reject_reason]}</p> : null}
  </div>
);

export const ProposalActions: React.FC<{
  readonly workspaceId: string;
  readonly proposal: ProposalRow;
  readonly canAct: boolean;
  readonly onDone?: (() => void) | undefined;
}> = ({ workspaceId, proposal, canAct, onDone }) => {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState<RejectReason>("WRONG_DECISION");
  const [rejecting, setRejecting] = useState(false);
  const settle = (): void => {
    void queryClient.invalidateQueries({ queryKey: processKeys.proposals(workspaceId) });
    void queryClient.invalidateQueries({ queryKey: processKeys.overview(workspaceId) });
    void queryClient.invalidateQueries({ queryKey: reviewKeys.all(workspaceId) });
    onDone?.();
  };
  const approve = useMutation({
    mutationFn: () => approveProposal(workspaceId, proposal.id),
    onSuccess: (out) => {
      toast.success(out.upload_path ? "Requested. Share the upload link with whoever holds the document." : "Applied.");
      settle();
    },
    onError: (error) => {
      toast.warning(actionError(error, "Could not apply the proposal."));
      settle();
    },
  });
  const reject = useMutation({
    mutationFn: () => rejectProposal(workspaceId, proposal.id, reason),
    onSuccess: () => {
      toast.success("Rejected. The agent will not propose this again until the item changes.");
      setRejecting(false);
      settle();
    },
    onError: (error) => toast.warning(actionError(error, "Could not reject the proposal.")),
  });
  const undo = useMutation({
    mutationFn: () => undoProposal(workspaceId, proposal.id),
    onSuccess: () => {
      toast.success("Undone. Nothing was applied; the item waits for a person.");
      settle();
    },
    onError: (error) => toast.warning(actionError(error, "Could not undo it.")),
  });
  if (!canAct || !LIVE_STATUSES.includes(proposal.status)) {
    return null;
  }
  const busy = approve.isPending || reject.isPending || undo.isPending;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {proposal.status === "AUTO_SCHEDULED" ? (
        <button type="button" className={BUTTON_SECONDARY} disabled={busy} onClick={() => undo.mutate()}>
          <Undo2 className="h-4 w-4" aria-hidden /> Undo
        </button>
      ) : null}
      <button type="button" className={BUTTON_PRIMARY} disabled={busy} onClick={() => approve.mutate()}>
        {approve.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Check className="h-4 w-4" aria-hidden />}
        {proposal.status === "AUTO_SCHEDULED" ? "Apply now" : "Approve"}
      </button>
      {rejecting ? (
        <>
          <label className="sr-only" htmlFor={`reject-${proposal.id}`}>Why</label>
          <select id={`reject-${proposal.id}`} className={`${SELECT} w-64`} value={reason} onChange={(e) => setReason(e.target.value as RejectReason)}>
            {REJECT_REASONS.map((r) => <option key={r} value={r}>{REJECT_REASON_LABELS[r]}</option>)}
          </select>
          <button type="button" className={BUTTON_SECONDARY} disabled={busy} onClick={() => reject.mutate()}>Reject</button>
          <button type="button" className={BUTTON_GHOST} onClick={() => setRejecting(false)}>Cancel</button>
        </>
      ) : (
        <button type="button" className={BUTTON_GHOST} disabled={busy} onClick={() => setRejecting(true)}>
          <X className="h-4 w-4" aria-hidden /> Reject
        </button>
      )}
    </div>
  );
};

const flagsText = (flags: Readonly<Record<string, number>>): string =>
  Object.entries(flags).filter(([, n]) => n > 0).map(([k, n]) => `${k.split("_").join(" ")} ×${n}`).join(", ");

const argument = (value: unknown): string => {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
};

/** The full proposal: summary, actions, evidence, the tool calls, and the fenced excerpts. */
export const ProposalPanel: React.FC<{
  readonly workspaceId: string;
  readonly proposalId: string;
  readonly canAct: boolean;
  readonly onClose?: () => void;
}> = ({ workspaceId, proposalId, canAct, onClose }) => {
  const detail = useQuery({
    queryKey: processKeys.proposal(workspaceId, proposalId),
    queryFn: () => getProposal(workspaceId, proposalId),
    enabled: Boolean(workspaceId && proposalId),
  });
  if (detail.isLoading) {
    return <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" />;
  }
  if (detail.isError || !detail.data) {
    return <p className="text-sm text-destructive">{errorMessage(detail.error, "Could not load the proposal.")}</p>;
  }
  const { proposal, evidence, tool_calls: calls, excerpts } = detail.data;
  return (
    <section className="space-y-4" aria-label="Proposal">
      <div className="flex items-start justify-between gap-3">
        <ProposalSummary proposal={proposal} />
        {onClose ? (
          <button type="button" className={BUTTON_GHOST} onClick={onClose} aria-label="Close the proposal">
            <X className="h-4 w-4" aria-hidden />
          </button>
        ) : null}
      </div>
      <ProposalActions workspaceId={workspaceId} proposal={proposal} canAct={canAct} />
      <p className={HINT}>
        Proposed {formatTimestamp(proposal.created_at)} on version {proposal.subject_version} of the item
        {proposal.decided_at ? ` · decided ${formatTimestamp(proposal.decided_at)}` : ""}
        {proposal.applied_at ? ` · applied ${formatTimestamp(proposal.applied_at)}` : ""}
        {proposal.resolution ? ` · ${proposal.resolution}` : ""}
      </p>

      <div>
        <h3 className={SECTION_TITLE}>Evidence</h3>
        <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
          {Object.entries(evidence).map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="text-muted-foreground">{key.split("_").join(" ")}</dt>
              <dd className="truncate font-mono">{argument(value)}</dd>
            </div>
          ))}
        </dl>
      </div>

      <div>
        <h3 className={SECTION_TITLE}>What the agent read and did</h3>
        <ol className="mt-1 space-y-1 text-xs">
          {calls.map((call) => (
            <li key={call.seq} className="flex flex-wrap items-baseline gap-2">
              <span className="w-5 text-right tabular-nums text-muted-foreground">{call.seq}.</span>
              <span className="font-semibold">{TOOL_LABELS[call.tool] ?? call.tool}</span>
              <span className={call.outcome === "OK" ? "text-emerald-700 dark:text-emerald-400" : "text-muted-foreground"}>{call.outcome}</span>
              <span className="min-w-0 break-all font-mono text-muted-foreground">
                {Object.entries(call.detail).map(([k, v]) => `${k}=${argument(v)}`).join(" · ")}
              </span>
            </li>
          ))}
        </ol>
      </div>

      <div>
        <h3 className={SECTION_TITLE}>What the source says</h3>
        <p className={HINT}>
          Quoted, untrusted text from the documents. The agent never decided from it — it chose from ids, scores and
          states — and nothing here is an instruction to anyone.
        </p>
        {excerpts.length === 0 ? <p className={`${HINT} mt-1`}>No excerpt for this item.</p> : null}
        <div className="mt-2 space-y-2">
          {excerpts.map((excerpt) => {
            const flags = flagsText(excerpt.injection_flags);
            return (
              <figure key={`${excerpt.label}-${excerpt.fence_nonce}`} className={`${SURFACE_INSET} p-3`}>
                <figcaption className="mb-1 flex flex-wrap items-center gap-2 text-xs font-semibold">
                  <Quote className="h-3.5 w-3.5" aria-hidden /> {excerpt.label}
                  {flags ? (
                    <span className="inline-flex items-center gap-1 rounded bg-destructive/10 px-1.5 py-0.5 text-[11px] text-destructive">
                      <AlertTriangle className="h-3 w-3" aria-hidden /> reads like instructions: {flags}
                    </span>
                  ) : null}
                </figcaption>
                <blockquote className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-l-2 border-border pl-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
                  {excerpt.fenced_text}
                </blockquote>
              </figure>
            );
          })}
        </div>
      </div>
    </section>
  );
};

export default ProposalPanel;
