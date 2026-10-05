/**
 * PHASE 4 — payment risk on the audit radar.
 *
 * Invoices whose vendor asks to be paid into a different bank account than
 * on its previous invoice (HIGH: confirm the change with the vendor by phone
 * before paying), and invoices with a suspiciously round total (LOW).
 * Contributors confirm or dismiss with a reason; viewers read.
 */

import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Landmark, Loader2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { BUTTON_GHOST, BUTTON_PRIMARY, HINT, SURFACE, TEXTAREA } from "@/components/ui/primitives";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { workItemDetailsPath } from "@/routes/tenantPaths";
import { ApiError } from "@/services/api/errors";
import {
  MIN_DISMISS_REASON,
  confirmPaymentRisk,
  dismissPaymentRisk,
  listPaymentRisk,
  type PaymentRiskFlag,
} from "@/services/api/paymentRisk";
import type { WorkspaceRole } from "@/types/tenancy";

const KIND_LABEL: Record<PaymentRiskFlag["kind"], string> = {
  BANK_ACCOUNT_CHANGED: "Bank account changed",
  ROUND_AMOUNT: "Round total",
};

const SEVERITY_TONE: Record<PaymentRiskFlag["severity"], string> = {
  HIGH: "bg-destructive/15 text-destructive",
  MEDIUM: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  LOW: "bg-muted text-muted-foreground",
};

interface PaymentRiskPanelProps {
  readonly workspaceId: string;
  readonly role: string;
}

export const PaymentRiskPanel: React.FC<PaymentRiskPanelProps> = ({ workspaceId, role }) => {
  const queryClient = useQueryClient();
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const [showSettled, setShowSettled] = useState(false);
  const [dismissing, setDismissing] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const canDecide = isAtLeast(role as WorkspaceRole, "CONTRIBUTOR");

  const flags = useQuery({
    queryKey: ["payment-risk", workspaceId, showSettled],
    queryFn: () => listPaymentRisk(workspaceId, showSettled ? undefined : "OPEN"),
    enabled: Boolean(workspaceId),
  });
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["payment-risk", workspaceId] });
  const confirm = useMutation({ mutationFn: (id: string) => confirmPaymentRisk(workspaceId, id), onSuccess: refresh });
  const dismiss = useMutation({
    mutationFn: (input: { id: string; reason: string }) => dismissPaymentRisk(workspaceId, input.id, input.reason),
    onSuccess: () => {
      setDismissing(null);
      setReason("");
      refresh();
    },
  });

  const items = flags.data?.items ?? [];
  const open = flags.data?.counts_by_status.OPEN ?? 0;
  const failure = confirm.error ?? dismiss.error;

  return (
    <section aria-label="Payment risk" className={`${SURFACE} space-y-3 p-4`}>
      <header className="flex flex-wrap items-center gap-2">
        <Landmark className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h2 className="text-sm font-semibold">Payment risk</h2>
        <span className={HINT}>{open} open</span>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={showSettled} onChange={(event) => setShowSettled(event.target.checked)} />
          Show settled
        </label>
      </header>

      {flags.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Loading…
        </p>
      ) : items.length === 0 ? (
        <p className={HINT}>No invoice changed its bank account or carries a suspiciously round total.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((flag) => (
            <li key={flag.id} className="rounded-md border border-border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${SEVERITY_TONE[flag.severity]}`}>
                  {flag.severity}
                </span>
                <span className="text-sm font-medium">{KIND_LABEL[flag.kind]}</span>
                {flag.document && (
                  <Link
                    to={workItemDetailsPath(orgSlug, workspaceSlug, flag.work_item_id)}
                    className="text-xs text-primary hover:underline"
                  >
                    {flag.document}
                  </Link>
                )}
                {flag.status !== "OPEN" && (
                  <span className="ml-auto text-[11px] uppercase text-muted-foreground">{flag.status}</span>
                )}
              </div>
              <p className="mt-1 text-sm">{flag.summary}</p>
              {flag.review_note && <p className={`mt-1 ${HINT}`}>Reviewer: {flag.review_note}</p>}

              {canDecide && flag.status === "OPEN" && (
                <div className="mt-2 space-y-2">
                  {dismissing === flag.id ? (
                    <>
                      <textarea
                        rows={2}
                        value={reason}
                        onChange={(event) => setReason(event.target.value)}
                        aria-label="Why this is not a risk"
                        placeholder="Why this is fine (at least 10 characters) — the next reviewer reads this."
                        className={TEXTAREA}
                      />
                      <div className="flex gap-2">
                        <button
                          type="button"
                          className={BUTTON_PRIMARY}
                          disabled={reason.trim().length < MIN_DISMISS_REASON || dismiss.isPending}
                          onClick={() => dismiss.mutate({ id: flag.id, reason: reason.trim() })}
                        >
                          Dismiss
                        </button>
                        <button type="button" className={BUTTON_GHOST} onClick={() => setDismissing(null)}>
                          Cancel
                        </button>
                      </div>
                    </>
                  ) : (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        className={BUTTON_PRIMARY}
                        disabled={confirm.isPending}
                        onClick={() => confirm.mutate(flag.id)}
                      >
                        Confirm risk
                      </button>
                      <button type="button" className={BUTTON_GHOST} onClick={() => setDismissing(flag.id)}>
                        Not a risk…
                      </button>
                    </div>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {failure && (
        <p role="alert" className="text-xs text-destructive">
          {failure instanceof ApiError ? failure.message : "That didn't save. Nothing was recorded."}
        </p>
      )}
    </section>
  );
};

export default PaymentRiskPanel;
