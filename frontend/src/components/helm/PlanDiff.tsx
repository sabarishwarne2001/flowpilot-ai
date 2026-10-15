import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronDown, FileCheck2, Lock, Undo2 } from "lucide-react";

import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { useHelm } from "@/components/helm/HelmContext";
import { ACTION_STATE_WORDS, ACTION_VERBS, GuardBadge, Money } from "@/components/helm/HelmBits";
import { helmCertificatePath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { approveAllHelmActions, approveHelmAction, helmKeys, rejectHelmAction, undoHelmAction } from "@/services/api/helm";
import { formatCalendarDate } from "@/utils/displayTime";
import type { HelmAction } from "@/types/helm";

const PAYMENT_ACTIONS = new Set(["hold_payment", "release_hold", "schedule_payment", "approve_payment", "mark_paid"]);

/** Owners and admins approve anything; the billing role approves payment actions (brief §5.4). */
export const canApprove = (role: string, kind: string): boolean =>
  role === "OWNER" || role === "ADMIN" || (role === "BILLING" && PAYMENT_ACTIONS.has(kind));

const CHECK_WORDS: Record<string, string> = {
  pass: "Passed",
  fail: "Failed",
  needs_approval: "Needs approval",
  not_applicable: "Not applicable",
};

const ActionRow: React.FC<{ readonly action: HelmAction; readonly readOnly: boolean }> = ({ action, readOnly }) => {
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
      toast.success(updated.state === "EXECUTED" ? "Approved and done." : "Approved.");
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
  const approvable = mine && action.state === "PROPOSED" && action.guard_passed;
  const rejectable = mine && (action.state === "PROPOSED" || action.state === "BLOCKED");
  const busy = approve.isPending || reject.isPending || undo.isPending;
  const checks = action.guard.checks ?? [];
  const detailsId = `helm-action-${action.id}`;

  return (
    <li className="helm-hairline border-t py-3 first:border-t-0" data-testid="helm-action">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2">
            <span className="helm-chip helm-chip-accent">{verb}</span>
            <span className="font-medium">{action.title}</span>
          </p>
          <p className="helm-muted mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs">
            {action.amount_micros !== null ? <Money micros={action.amount_micros} currency={action.currency} /> : null}
            {action.run_date ? <span>on {formatCalendarDate(action.run_date)}</span> : null}
            {action.value_micros ? (
              <span>
                value <Money micros={action.value_micros} currency={action.currency} />
              </span>
            ) : null}
            {action.workspace_name ? <span>{action.workspace_name}</span> : null}
            <span>{ACTION_STATE_WORDS[action.state] ?? action.state}</span>
            {action.irreversible ? <span>cannot be undone once sent</span> : null}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <GuardBadge action={action} />
          {approvable ? (
            <button
              type="button"
              className="helm-btn helm-btn-primary"
              disabled={busy}
              onClick={() => approve.mutate()}
              aria-label={`Approve: ${action.title}`}
            >
              Approve
            </button>
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
            <div id={detailsId} className="helm-panel-raised mt-2 space-y-2 p-3 text-xs">
              <p>{action.explanation}</p>
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
  const approvable = actions.filter(
    (a) => a.state === "PROPOSED" && a.guard_passed && a.readable && canApprove(role, a.kind),
  );
  const applyAll = useMutation({
    mutationFn: () => approveAllHelmActions(organizationId, book?.id ?? ""),
    onSuccess: (result) => {
      toast.success(
        result.skipped
          ? `Approved ${result.approved}. ${result.skipped} need someone else or were refused.`
          : `Approved ${result.approved}.`,
      );
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
        <div className="mb-2 flex justify-end">
          <button type="button" className="helm-btn helm-btn-primary" onClick={() => setConfirming(true)}>
            Apply plan ({approvable.length})
          </button>
        </div>
      ) : null}
      <ul aria-label="Plan">
        {actions.map((action) => (
          <ActionRow key={action.id} action={action} readOnly={readOnly} />
        ))}
      </ul>
      <ConfirmDialog
        open={confirming}
        title="Apply the plan?"
        message={`Approve the ${approvable.length} actions that passed the Guard. Each is checked again just before it runs. Payments are scheduled for your payment run; Helm never moves money.`}
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
