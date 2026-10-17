import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronDown, FileCheck2, Lock, Undo2 } from "lucide-react";

import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { useHelm } from "@/components/helm/HelmContext";
import { ACTION_STATE_WORDS, ACTION_VERBS, GuardBadge, Money, ROLE_WORDS, rolesWords } from "@/components/helm/HelmBits";
import { VerbChip } from "@/components/helm/ui/VerbChip";
import { helmCertificatePath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { approveAllHelmActions, approveHelmAction, helmKeys, rejectHelmAction, undoHelmAction } from "@/services/api/helm";
import { formatCalendarDate, formatNumber } from "@/utils/displayTime";
import type { HelmAction } from "@/types/helm";

const PAYMENT_ACTIONS = new Set(["hold_payment", "release_hold", "schedule_payment", "approve_payment", "mark_paid"]);

/** Owners and admins approve anything; the billing role approves payment actions (brief §5.4). */
export const canApprove = (role: string, kind: string): boolean =>
  role === "OWNER" || role === "ADMIN" || (role === "BILLING" && PAYMENT_ACTIONS.has(kind));

const ORDINALS: Record<number, string> = { 2: "second", 3: "third" };

/** On an approval chain (Enterprise): the viewer already approved and the payment waits for someone else. */
const waitingForOthers = (action: HelmAction): boolean =>
  action.approvals.some((a) => a.by_you) && action.approvals_given < action.required_approvals;

/** On an approval chain that names roles, whether the viewer's role is one whose approval counts. */
const roleCounts = (action: HelmAction, role: string): boolean =>
  !action.approver_roles || (action.approver_roles as readonly string[]).includes(role);

/** Whether this viewer may approve the action now (the server checks the same, and says why if not). */
export const mayApprove = (action: HelmAction, role: string): boolean =>
  action.state === "PROPOSED" &&
  action.guard_passed &&
  action.readable &&
  canApprove(role, action.kind) &&
  roleCounts(action, role) &&
  !waitingForOthers(action);

/** "Add the second approval" once someone approved a payment on a chain; "Approve" otherwise. */
const approveLabel = (action: HelmAction): string =>
  action.required_approvals > 1 && action.approvals_given > 0 && action.approvals_given < action.required_approvals
    ? `Add the ${ORDINALS[action.approvals_given + 1] ?? "next"} approval`
    : "Approve";

/** "1 of 2 approvals: owner (you)", with a pip per approval the payment needs. */
const ApprovalProgress: React.FC<{ readonly action: HelmAction }> = ({ action }) => {
  if (action.required_approvals <= 1) {
    return null;
  }
  const given = Math.min(action.approvals_given, action.required_approvals);
  const who = action.approvals
    .filter((a) => !a.by_helm && a.role)
    .map((a) => `${ROLE_WORDS[a.role ?? ""] ?? (a.role ?? "").toLowerCase()}${a.by_you ? " (you)" : ""}`);
  return (
    <span className="inline-flex items-center gap-1.5" data-testid="helm-approvals">
      <span className="helm-pips" aria-hidden="true">
        {Array.from({ length: action.required_approvals }, (_, index) => (
          <span key={index} className={`helm-pip ${index < given ? "helm-pip-on" : ""}`} />
        ))}
      </span>
      <span>
        <span className="helm-figure">{formatNumber(given)}</span> of{" "}
        <span className="helm-figure">{formatNumber(action.required_approvals)}</span> approvals
        {who.length > 0 ? `: ${who.join(", ")}` : `, from ${rolesWords(action.approver_roles)}`}
      </span>
    </span>
  );
};

const CHECK_WORDS: Record<string, string> = {
  pass: "Passed",
  fail: "Failed",
  needs_approval: "Needs approval",
  not_applicable: "Not applicable",
};

interface Draft {
  readonly subject: string;
  readonly text: string;
  readonly to: string | null;
}

const draftOf = (action: HelmAction): Draft | null => {
  const raw = action.result["draft"];
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const found = raw as Record<string, unknown>;
  return typeof found["subject"] === "string" && typeof found["text"] === "string"
    ? { subject: found["subject"], text: found["text"], to: typeof found["to"] === "string" ? found["to"] : null }
    : null;
};

/** The email a draft action wrote (a dispute, a statement reconciliation): to copy, or as sent. */
const DraftEmail: React.FC<{ readonly action: HelmAction; readonly draft: Draft }> = ({ action, draft }) => {
  const sent = Boolean(action.result["sent"]);
  const copy = (): void => {
    void navigator.clipboard
      .writeText(`Subject: ${draft.subject}\n\n${draft.text}`)
      .then(() => toast.success("The email is copied."))
      .catch(() => toast.error("Your browser did not allow copying. Select the text instead."));
  };
  return (
    <div className="helm-panel-raised mt-2 space-y-2 p-3 text-xs" data-testid="helm-draft-email">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold">
          {sent ? `Sent${draft.to ? ` to ${draft.to}` : ""}` : "The drafted email"}: {draft.subject}
        </p>
        <button type="button" className="helm-btn" onClick={copy} aria-label={`Copy the email: ${draft.subject}`}>
          Copy the email
        </button>
      </div>
      <pre className="whitespace-pre-wrap break-words font-sans">{draft.text}</pre>
    </div>
  );
};

/** A reconciliation's differences, each with the evidence it rests on, in words. */
const Differences: React.FC<{ readonly action: HelmAction }> = ({ action }) => {
  const raw = action.params["differences"];
  if (!Array.isArray(raw) || raw.length === 0) {
    return null;
  }
  const items = raw.filter((d): d is { code: string; summary: string } =>
    Boolean(d) && typeof (d as { summary?: unknown }).summary === "string");
  return (
    <div>
      <p className="font-semibold">The differences, from the statement and the ledger:</p>
      <ul className="mt-1 list-disc space-y-1 pl-5" data-testid="helm-differences">
        {items.map((d, index) => (
          <li key={`${d.code}-${index}`}>{d.summary}</li>
        ))}
      </ul>
      <p className="mt-1">
        Approving drafts the reconciliation email listing each difference. Helm sends it only in Act mode, to a known
        contact.
      </p>
    </div>
  );
};

const ActionRow: React.FC<{ readonly action: HelmAction; readonly readOnly: boolean; readonly fresh?: boolean }> = ({
  action,
  readOnly,
  fresh = false,
}) => {
  const { organizationId, orgSlug, role, book, withBook } = useHelm();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  const refresh = (): void => {
    if (book) {
      void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
    }
    void queryClient.invalidateQueries({ queryKey: helmKeys.overview(organizationId) });
  };

  const approve = useMutation({
    mutationFn: () => approveHelmAction(organizationId, action.id),
    onSuccess: (updated) => {
      toast.success(
        updated.state === "EXECUTED"
          ? "Approved and done."
          : updated.state === "PROPOSED" && updated.required_approvals > 1
            ? `Your approval is in: ${formatNumber(updated.approvals_given)} of ${formatNumber(updated.required_approvals)}. It now waits for someone else.`
            : "Approved.",
      );
      refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not approve this action.")),
  });
  const reject = useMutation({
    mutationFn: () => rejectHelmAction(organizationId, action.id, reason.trim()),
    onSuccess: () => {
      toast.success("Rejected. Helm will plan around it.");
      setRejecting(false);
      setReason("");
      refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not reject this action.")),
  });
  const undo = useMutation({
    mutationFn: () => undoHelmAction(organizationId, action.id),
    onSuccess: () => {
      toast.success("Undone.");
      refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not undo this action.")),
  });

  const verb = ACTION_VERBS[action.kind] ?? action.kind;
  const mine = !readOnly && action.readable && canApprove(role, action.kind);
  const approvable = mine && mayApprove(action, role);
  const waiting = mine && action.state === "PROPOSED" && waitingForOthers(action);
  const notYourRole = mine && action.state === "PROPOSED" && !roleCounts(action, role);
  const rejectable = mine && (action.state === "PROPOSED" || action.state === "BLOCKED");
  const busy = approve.isPending || reject.isPending || undo.isPending;
  const checks = action.guard.checks ?? [];
  const detailsId = `helm-action-${action.id}`;
  const draft = action.readable ? draftOf(action) : null;

  return (
    <li
      className={`helm-plan-row relative py-3.5 pl-7 ${fresh ? "helm-row-in" : ""}`}
      data-testid="helm-action"
      data-state={action.state}
    >
      <span className="helm-plan-node" data-guard={action.guard_passed && action.state !== "BLOCKED" ? "passed" : "blocked"} aria-hidden="true" />
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <VerbChip kind={action.kind} verb={verb} />
            <span className="font-medium">{action.title}</span>
          </p>
          <p className="helm-muted mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            {action.amount_micros !== null ? (
              <Money micros={action.amount_micros} currency={action.currency} className="text-[13px] font-semibold text-[hsl(var(--helm-ink))]" />
            ) : null}
            {action.run_date ? <span>on {formatCalendarDate(action.run_date)}</span> : null}
            {action.value_micros ? (
              <span>
                value <Money micros={action.value_micros} currency={action.currency} />
              </span>
            ) : null}
            {action.workspace_name ? <span>{action.workspace_name}</span> : null}
            <span>{ACTION_STATE_WORDS[action.state] ?? action.state}</span>
            {action.irreversible ? <span>cannot be undone once sent</span> : null}
            <ApprovalProgress action={action} />
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <GuardBadge action={action} />
          {approvable ? (
            <button
              type="button"
              className="helm-btn helm-btn-signal"
              disabled={busy}
              onClick={() => approve.mutate()}
              aria-label={`${approveLabel(action)}: ${action.title}`}
            >
              {approveLabel(action)}
            </button>
          ) : waiting ? (
            <span className="helm-muted max-w-[16rem] text-xs" data-testid="helm-approval-waiting">
              You approved. Waiting for someone else to add the {ORDINALS[action.approvals_given + 1] ?? "next"} approval.
            </span>
          ) : notYourRole ? (
            <span className="helm-muted max-w-[16rem] text-xs">Approved by {rolesWords(action.approver_roles)} only.</span>
          ) : null}
          {rejectable ? (
            <button
              type="button"
              className="helm-btn"
              disabled={busy}
              onClick={() => setRejecting(true)}
              aria-label={`Reject: ${action.title}`}
            >
              Reject
            </button>
          ) : null}
          {action.state === "EXECUTED" && action.undoable && !readOnly ? (
            <button type="button" className="helm-btn" disabled={busy} onClick={() => undo.mutate()}>
              <Undo2 className="h-3.5 w-3.5" aria-hidden="true" />
              Undo
            </button>
          ) : null}
        </div>
      </div>
      {action.readable ? (
        <div className="mt-2">
          <button
            type="button"
            className="helm-muted inline-flex items-center gap-1 text-xs hover:underline"
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={() => setOpen((value) => !value)}
          >
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
            Why, and what the Guard checked
          </button>
          {open ? (
            <div id={detailsId} className="helm-panel-raised helm-row-in mt-2 space-y-2 p-3.5 text-xs">
              {action.kind === "draft_reconciliation" ? <Differences action={action} /> : <p>{action.explanation}</p>}
              {checks.length > 0 ? (
                <ul className="space-y-1">
                  {checks.map((check) => (
                    <li key={check.id} className="flex flex-col gap-0.5 sm:flex-row sm:gap-2">
                      <span className={`w-28 shrink-0 font-semibold ${check.result === "fail" ? "helm-state-breached" : ""}`}>
                        {CHECK_WORDS[check.result] ?? check.result}
                      </span>
                      <span>
                        <span className="font-medium">{check.title}.</span> {check.detail}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {action.certificate_id ? (
                <Link
                  to={withBook(helmCertificatePath(orgSlug, action.certificate_id))}
                  className="helm-accent inline-flex items-center gap-1 font-semibold hover:underline"
                >
                  <FileCheck2 className="h-3.5 w-3.5" aria-hidden="true" />
                  Read the certificate
                </Link>
              ) : null}
            </div>
          ) : null}
          {draft ? <DraftEmail action={action} draft={draft} /> : null}
        </div>
      ) : (
        <p className="helm-muted mt-2 inline-flex items-center gap-1 text-xs">
          <Lock className="h-3 w-3" aria-hidden="true" />
          On a document in {action.workspace_name ?? "a workspace"} you cannot read. Ask a workspace admin for access.
        </p>
      )}
      <ConfirmDialog
        open={rejecting}
        title="Reject this action?"
        message="Helm will not do it, and plans around it from now on. Say why, for the record."
        confirmText="Reject"
        tone="danger"
        loading={reject.isPending}
        confirmDisabled={reason.trim().length === 0}
        input={{ label: "Reason", value: reason, onChange: setReason, maxLength: 1000, placeholder: "For example: already paid by cheque" }}
        onConfirm={() => reject.mutate()}
        onCancel={() => setRejecting(false)}
      />
    </li>
  );
};

/** The plan as a diff, with approve and reject per row and Apply plan for everything that passed. */
export const PlanDiff: React.FC<{ readonly actions: readonly HelmAction[]; readonly readOnly: boolean }> = ({ actions, readOnly }) => {
  const { organizationId, role, book } = useHelm();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  // Rows a new plan adds rise in; the ones on screen from the start do not (the panel already entered).
  const seen = useRef<Set<string> | null>(null);
  if (seen.current === null) {
    seen.current = new Set(actions.map((a) => a.id));
  }
  useEffect(() => {
    actions.forEach((a) => seen.current?.add(a.id));
  }, [actions]);
  const approvable = actions.filter((a) => mayApprove(a, role));
  const chained = approvable.filter((a) => a.approvals_given + 1 < a.required_approvals).length;
  const applyAll = useMutation({
    mutationFn: () => approveAllHelmActions(organizationId, book?.id ?? ""),
    onSuccess: (result) => {
      const parts = [`Approved ${formatNumber(result.approved)}.`];
      if (result.waiting) {
        parts.push(`${formatNumber(result.waiting)} on an approval chain now wait for another person.`);
      }
      if (result.skipped) {
        parts.push(`${formatNumber(result.skipped)} need someone else or were refused.`);
      }
      if (result.already_decided) {
        parts.push(`${formatNumber(result.already_decided)} had just been decided in another tab or by another person.`);
      }
      toast.success(parts.join(" "));
      setConfirming(false);
      if (book) {
        void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
      }
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not apply the plan.")),
  });

  if (actions.length === 0) {
    return <p className="helm-muted py-4 text-sm">Nothing to do. Every active mandate is holding with what Helm can see.</p>;
  }

  return (
    <div>
      {!readOnly && approvable.length > 0 ? (
        <div className="mb-3 flex justify-end">
          <button type="button" className="helm-btn helm-btn-primary helm-btn-hero" onClick={() => setConfirming(true)}>
            Apply plan ({formatNumber(approvable.length)})
          </button>
        </div>
      ) : null}
      <ul aria-label="Plan" className="helm-plan-list">
        {actions.map((action) => (
          <ActionRow key={action.id} action={action} readOnly={readOnly} fresh={!seen.current?.has(action.id)} />
        ))}
      </ul>
      <ConfirmDialog
        open={confirming}
        title="Apply the plan?"
        message={`Approve the ${formatNumber(approvable.length)} actions that passed the Guard. Each is checked again just before it runs. Payments are scheduled for your payment run; Helm never moves money.${
          chained > 0
            ? ` ${formatNumber(chained)} of them are on an approval chain: yours is one of the approvals they need, and they then wait for another person.`
            : ""
        }`}
        confirmText="Apply plan"
        tone="primary"
        initialFocus="confirm"
        loading={applyAll.isPending}
        onConfirm={() => applyAll.mutate()}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
};

export default PlanDiff;
