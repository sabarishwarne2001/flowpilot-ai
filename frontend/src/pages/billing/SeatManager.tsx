import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, RefreshCw, Users } from "lucide-react";

import { getSubscriptionState, syncSeats } from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";
import { formatMicros } from "@/types/billing";

export interface SeatManagerProps {
  readonly organizationId: string;
  readonly canManageBilling?: boolean;
}

export const SeatManager: React.FC<SeatManagerProps> = ({
  organizationId,
  canManageBilling = false,
}) => {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const { data: state, isLoading } = useQuery({
    queryKey: billingKeys.subscription(organizationId),
    queryFn: () => getSubscriptionState(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const sync = useMutation({
    mutationFn: () => syncSeats(organizationId, { reason: "owner_requested" }),
    onSuccess: (updated) => {
      queryClient.setQueryData(
        billingKeys.subscription(organizationId),
        updated,
      );
      setConfirming(false);
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading seats…
      </div>
    );
  }

  if (!state) {
    return null;
  }

  if (!state.has_billing_account) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium">Seats</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          This organization has {state.seats_billable}{" "}
          {state.seats_billable === 1 ? "member" : "members"} and no
          subscription yet. Choose a plan to start billing.
        </p>
      </section>
    );
  }

  const drift = state.seat_drift_delta;
  const currency = state.currency ?? "USD";

  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center gap-2">
        <Users className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <h2 className="text-sm font-medium">Seats</h2>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-4">
        <div>
          <dt className="text-xs text-muted-foreground">In use</dt>
          <dd className="mt-0.5 text-2xl font-semibold tabular-nums">
            {state.seats_billable}
          </dd>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Active organization members
          </p>
        </div>

        <div>
          <dt className="text-xs text-muted-foreground">Paid for</dt>
          <dd className="mt-0.5 text-2xl font-semibold tabular-nums">
            {state.seats_purchased}
          </dd>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Billed at your provider
          </p>
        </div>
      </dl>

      {drift !== 0 && (
        <div
          className={[
            "mt-4 rounded-md border px-3 py-2.5",
            drift > 0
              ? "border-amber-500/40 bg-amber-500/5"
              : "border-border bg-muted/40",
          ].join(" ")}
        >
          <p className="flex items-start gap-2 text-sm">
            <AlertTriangle
              className={`mt-0.5 h-4 w-4 shrink-0 ${drift > 0 ? "text-amber-600" : "text-muted-foreground"}`}
              aria-hidden="true"
            />
            <span>
              {drift > 0 ? (
                <>
                  <strong className="font-medium">
                    {drift} {drift === 1 ? "member is" : "members are"} not
                    being billed.
                  </strong>{" "}
                  More people have active memberships than you are paying for.
                  Reconciling will add {drift}{" "}
                  {drift === 1 ? "seat" : "seats"} to your subscription.
                </>
              ) : (
                <>
                  <strong className="font-medium">
                    You are paying for {Math.abs(drift)} unused{" "}
                    {Math.abs(drift) === 1 ? "seat" : "seats"}.
                  </strong>{" "}
                  Reconciling will reduce your subscription to{" "}
                  {state.seats_billable}.
                </>
              )}
            </span>
          </p>

          {canManageBilling && !confirming && (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="mt-2.5 inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-muted"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Reconcile seats
            </button>
          )}
        </div>
      )}

      {confirming && (
        <div
          role="dialog"
          aria-label="Confirm seat change"
          className="mt-4 rounded-md border border-border bg-background p-3"
        >
          <p className="text-sm font-medium">
            Change your subscription from {state.seats_purchased} to{" "}
            {state.seats_billable}{" "}
            {state.seats_billable === 1 ? "seat" : "seats"}?
          </p>

          <p className="mt-2 text-xs text-muted-foreground">
            {drift > 0
              ? "Adding seats mid-period is charged pro rata for the days remaining. "
              : "Removing seats mid-period issues a pro-rata credit. "}
            The exact amount is calculated by your payment provider and shown
            on your next invoice — this page does not estimate it.
          </p>

          {state.subscription && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              Current period ends{" "}
              {new Date(
                state.subscription.current_period_end,
              ).toLocaleDateString()}
              .
            </p>
          )}

          {sync.isError && (
            <p role="alert" className="mt-2 text-xs text-destructive">
              The change didn&apos;t go through. Your seats are unchanged.
            </p>
          )}

          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setConfirming(false)}
              disabled={sync.isPending}
              className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => sync.mutate()}
              disabled={sync.isPending}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
            >
              {sync.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Confirm
            </button>
          </div>
        </div>
      )}

      {drift === 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          Seats match membership. Nothing to reconcile.
        </p>
      )}

      <p className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground">
        Seats follow membership. To reduce your bill, remove members in
        Members — changing a number here would be overwritten by the next
        directory sync.
        {state.delinquent_since && (
          <>
            {" "}
            Payment has been outstanding since{" "}
            {new Date(state.delinquent_since).toLocaleDateString()}.
          </>
        )}
      </p>

      {state.subscription && (
        <p className="mt-1 text-xs text-muted-foreground">
          Plan: {state.subscription.quota_tier_key} ·{" "}
          {state.subscription.status}
          {state.subscription.cancel_at_period_end && " · cancels at period end"}
          {" · "}
          {formatMicros(0, currency).replace(/[\d.,\s]/g, "")} billing
        </p>
      )}
    </section>
  );
};

export default SeatManager;
