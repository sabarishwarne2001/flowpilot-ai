import React, { useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { ApiError } from "@/services/api/errors";
import { confirmPlanChange, previewPlanChange, withdrawPlanChange } from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";
import { formatMicros } from "@/types/billing";
import { formatTimestampDate } from "@/utils/displayTime";

/**
 * Website and payments session (C6). A plan change on a Dodo subscription, made here rather than in
 * the billing portal (Dodo's portal manages the payment method, invoices and cancellation, not
 * plans). The owner sees Dodo's own figure before confirming: what is charged now (only the days
 * left, at the price difference) for an upgrade, which takes effect at once; for a downgrade,
 * nothing now and the date it takes effect (the renewal), after anything that must change first.
 */
export const DodoPlanChange: React.FC<{
  readonly organizationId: string;
  readonly tierKey: string;
  readonly seats: number;
  readonly canChange: boolean;
  readonly onDone: () => void;
}> = ({ organizationId, tierKey, seats, canChange, onDone }) => {
  const queryClient = useQueryClient();
  const preview = useMutation({ mutationFn: () => previewPlanChange(organizationId, { quota_tier_key: tierKey, seats }) });
  const confirm = useMutation({
    mutationFn: (expected: number) =>
      confirmPlanChange(organizationId, { quota_tier_key: tierKey, seats, expected_amount_micros: expected }),
    onSuccess: (done) => {
      toast.success(
        done.effective === "NOW"
          ? `You're on ${done.plan_name} now.`
          : `${done.plan_name} starts on ${formatTimestampDate(done.effective_at)}.`,
      );
      void queryClient.invalidateQueries({ queryKey: billingKeys.all(organizationId) });
      onDone();
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.message : "The plan could not be changed. Nothing was charged.");
      preview.mutate();
    },
  });

  const { mutate } = preview;
  useEffect(() => {
    mutate();
  }, [mutate, tierKey, seats]);

  if (preview.isPending || !preview.data) {
    return preview.isError ? (
      <p role="alert" className="text-sm text-destructive">
        {preview.error instanceof ApiError ? preview.error.message : "The change could not be priced. Try again in a moment."}
      </p>
    ) : (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Asking Dodo Payments for the exact amount…
      </p>
    );
  }
  const shown = preview.data;
  const blocked = shown.blockers.length > 0;
  return (
    <div className="space-y-2" data-testid="plan-change-preview">
      {shown.direction === "UPGRADE" ? (
        <p className="text-sm text-foreground">
          <strong>{shown.plan_name}</strong>, {shown.seats} {shown.seats === 1 ? "seat" : "seats"}, from now. Charged
          today: <strong data-testid="plan-change-amount">{formatMicros(shown.amount_now_micros, shown.currency)}</strong>{" "}
          for the rest of this billing period. Then {formatMicros(shown.next_renewal_micros, shown.currency)} each period.
        </p>
      ) : (
        <p className="text-sm text-foreground">
          <strong>{shown.plan_name}</strong>, {shown.seats} {shown.seats === 1 ? "seat" : "seats"}, from{" "}
          <strong data-testid="plan-change-date">{formatTimestampDate(shown.effective_at)}</strong>, your next renewal.
          Nothing is charged today; you keep the current plan until then. From then:{" "}
          {formatMicros(shown.next_renewal_micros, shown.currency)} each period.
        </p>
      )}
      {blocked && (
        <ul className="list-disc space-y-1 pl-5 text-sm text-destructive" data-testid="plan-change-blockers">
          {shown.blockers.map((b) => (
            <li key={b.code}>{b.message}</li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">Prices exclude taxes; Dodo Payments, our merchant of record, adds them.</p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => confirm.mutate(shown.amount_now_micros)}
          disabled={!canChange || blocked || confirm.isPending}
          className="fp-btn-primary inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
        >
          {confirm.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
          {shown.direction === "UPGRADE"
            ? `Upgrade and pay ${formatMicros(shown.amount_now_micros, shown.currency)}`
            : `Schedule the change for ${formatTimestampDate(shown.effective_at)}`}
        </button>
        <button
          type="button"
          onClick={onDone}
          disabled={confirm.isPending}
          className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground hover:bg-muted disabled:opacity-60"
        >
          Back
        </button>
      </div>
      {!canChange && <p className="text-xs text-muted-foreground">Only an organization owner can change the plan.</p>}
    </div>
  );
};

/** A downgrade waiting for the renewal, with a way to withdraw it. */
export const ScheduledPlanChange: React.FC<{
  readonly organizationId: string;
  readonly planName: string;
  readonly seats: number | null | undefined;
  readonly effectiveAt: string;
  readonly canChange: boolean;
}> = ({ organizationId, planName, seats, effectiveAt, canChange }) => {
  const queryClient = useQueryClient();
  const withdraw = useMutation({
    mutationFn: () => withdrawPlanChange(organizationId),
    onSuccess: () => {
      toast.success("The scheduled change is withdrawn.");
      void queryClient.invalidateQueries({ queryKey: billingKeys.all(organizationId) });
    },
    onError: () => toast.error("The scheduled change could not be withdrawn. Try again."),
  });
  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-border bg-muted/20 px-4 py-2 text-sm" data-testid="scheduled-plan-change">
      <span>
        Changing to <strong>{planName}</strong>
        {seats ? `, ${seats} ${seats === 1 ? "seat" : "seats"}` : ""} on {formatTimestampDate(effectiveAt)}, at your renewal.
      </span>
      {canChange && (
        <button
          type="button"
          onClick={() => withdraw.mutate()}
          disabled={withdraw.isPending}
          className="rounded-md border border-border bg-background px-2.5 py-1 text-xs hover:bg-muted disabled:opacity-60"
        >
          Keep the current plan
        </button>
      )}
    </div>
  );
};
