import { formatTimestampDate } from "@/utils/displayTime";
import React, { useRef, useState } from "react";
import { useDialogFocus } from "@/hooks/useDialogFocus";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, Minus, Plus, RefreshCw, Users } from "lucide-react";

import { changeSeats, fetchSeatPriceBook, getSubscriptionState, syncSeats } from "@/services/api/billing";
import { errorMessage } from "@/services/api/errors";
import { billingKeys } from "@/services/api/queryKeys";
import { formatMoneyMicros } from "@/utils/formatters";

export interface SeatManagerProps {
  readonly organizationId: string;
  readonly canManageBilling?: boolean;
}

const planName = (key: string | null | undefined): string =>
  key ? key.charAt(0).toUpperCase() + key.slice(1) : "Current";

const seatsWord = (count: number): string => (count === 1 ? "seat" : "seats");

/**
 * Seats (campaign session 1).
 *
 * The organization is the customer and every member occupies one seat. Free
 * includes the seats its plan declares; a paid plan holds the seats its
 * subscription bought. Adding someone past them needs a seat bought first, so
 * this panel is where owners, admins and billing managers buy seats (the price
 * shown and confirmed before anything is charged) and release unused ones. The
 * numbers are the server's: the same check refuses an invitation past them.
 */
export const SeatManager: React.FC<SeatManagerProps> = ({
  organizationId,
  canManageBilling = false,
}) => {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<number | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reconciling, setReconciling] = useState(false);

  const { data: state, isLoading } = useQuery({
    queryKey: billingKeys.subscription(organizationId),
    queryFn: () => getSubscriptionState(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const purchased = state?.seats_purchased ?? 0;
  const desired = target ?? purchased;
  const adding = Math.max(0, desired - purchased);

  const quote = useQuery({
    queryKey: [...billingKeys.subscription(organizationId), "seat-quote", adding],
    queryFn: () => fetchSeatPriceBook(organizationId, adding),
    enabled: reviewing && adding > 0,
    staleTime: 0,
    retry: false,
  });

  const settle = (updated: Awaited<ReturnType<typeof changeSeats>>) => {
    queryClient.setQueryData(billingKeys.subscription(organizationId), updated);
    void queryClient.invalidateQueries({ queryKey: billingKeys.all(organizationId) });
    setReviewing(false);
    setReconciling(false);
    setTarget(null);
  };

  const change = useMutation({
    mutationFn: () =>
      changeSeats(organizationId, {
        seats: desired,
        confirmed_unit_price_micros: adding > 0 ? (quote.data?.unit_price_micros ?? null) : null,
      }),
    onSuccess: settle,
  });

  const sync = useMutation({
    mutationFn: () => syncSeats(organizationId, { reason: "owner_requested" }),
    onSuccess: settle,
  });

  const dialogRef = useRef<HTMLDivElement | null>(null);
  useDialogFocus(dialogRef, () => setReviewing(false), {
    open: reviewing,
    busy: change.isPending,
    trap: false,
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

  const capacity = state.seat_capacity;
  const used = state.seats_used;
  const pending = state.seats_pending_invitations;
  const members = used - pending;
  const paid = state.seat_capacity_source === "PURCHASED";
  const currency = state.currency ?? "USD";
  const minimum = Math.max(1, used);
  const releasing = desired < purchased;
  const unused = paid && capacity !== null ? Math.max(0, capacity - used) : 0;
  const overBy = capacity !== null ? Math.max(0, used - capacity) : 0;

  return (
    <section className="rounded-lg border border-border bg-card p-4" aria-labelledby="seats-heading">
      <div className="flex items-center gap-2">
        <Users className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <h2 id="seats-heading" className="text-sm font-medium">Seats</h2>
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-4">
        <div>
          <dt className="text-xs text-muted-foreground">In use</dt>
          <dd className="mt-0.5 text-2xl font-semibold tabular-nums">{used}</dd>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {members} {members === 1 ? "member" : "members"}
            {pending > 0 && `, ${pending} pending ${pending === 1 ? "invitation" : "invitations"}`}
          </p>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{paid ? "Paid for" : "Included"}</dt>
          <dd className="mt-0.5 text-2xl font-semibold tabular-nums">{capacity ?? "No limit"}</dd>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {paid ? "On your subscription" : `On the ${planName(state.plan_key)} plan`}
          </p>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Available</dt>
          <dd className="mt-0.5 text-2xl font-semibold tabular-nums">{state.seats_available ?? "—"}</dd>
          <p className="mt-0.5 text-xs text-muted-foreground">For new members and invitations</p>
        </div>
      </dl>

      {overBy > 0 && (
        <p role="status" className="mt-4 flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2.5 text-sm">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
          <span>
            {overBy} more {overBy === 1 ? "person holds a seat" : "people hold seats"} than{" "}
            {paid ? "the subscription pays for" : "the plan includes"}. Nobody new can join until{" "}
            {paid ? "seats are added below" : "the plan is upgraded or people are removed"}.
          </span>
        </p>
      )}

      {!paid && state.seat_capacity_source === "PLAN" && (
        <p className="mt-4 text-sm text-muted-foreground">
          The {planName(state.plan_key)} plan includes {capacity} {seatsWord(capacity ?? 0)}. To add more
          people, choose a paid plan below; paid plans are billed per seat and you choose how many.
        </p>
      )}

      {paid && state.can_manage_seats && (
        <div className="mt-4 border-t border-border pt-3">
          <p className="text-xs font-medium text-muted-foreground" id="seat-stepper-label">
            Seats on the subscription
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2" role="group" aria-labelledby="seat-stepper-label">
            <button
              type="button"
              aria-label="One seat fewer"
              onClick={() => setTarget(Math.max(minimum, desired - 1))}
              disabled={desired <= minimum || change.isPending}
              className="rounded-md border border-border p-1.5 hover:bg-muted disabled:opacity-40"
            >
              <Minus className="h-3.5 w-3.5" />
            </button>
            <output aria-live="polite" className="min-w-[3ch] text-center text-lg font-semibold tabular-nums">
              {desired}
            </output>
            <button
              type="button"
              aria-label="One seat more"
              onClick={() => setTarget(Math.min(10_000, desired + 1))}
              disabled={change.isPending}
              className="rounded-md border border-border p-1.5 hover:bg-muted disabled:opacity-40"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
            {unused > 0 && desired === purchased && (
              <button
                type="button"
                onClick={() => setTarget(minimum)}
                className="text-xs font-medium text-primary hover:underline"
              >
                Remove {unused} unused {seatsWord(unused)}
              </button>
            )}
            {desired !== purchased && !reviewing && (
              <button
                type="button"
                onClick={() => setReviewing(true)}
                className="fp-btn-primary ml-auto rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90"
              >
                Review change
              </button>
            )}
          </div>
          {desired <= minimum && used > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              {used} {seatsWord(used)} are in use; remove people or revoke invitations to go lower.
            </p>
          )}
        </div>
      )}

      {reviewing && desired !== purchased && (
        <div
          ref={dialogRef}
          role="dialog"
          aria-label="Confirm seat change"
          className="mt-4 rounded-md border border-border bg-background p-3"
        >
          <p className="text-sm font-medium">
            Change the subscription from {purchased} to {desired} {seatsWord(desired)}?
          </p>

          {adding > 0 ? (
            quote.isLoading ? (
              <p className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Getting the price…
              </p>
            ) : quote.data && quote.data.unit_price_micros !== null ? (
              <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                <p>
                  <strong className="font-medium text-foreground">
                    {formatMoneyMicros(quote.data.unit_price_micros, quote.data.currency)} per seat per month
                  </strong>{" "}
                  for {adding} more {seatsWord(adding)}.
                </p>
                <p>
                  {quote.data.proration_micros !== null
                    ? `Charged now for the rest of this period: ${formatMoneyMicros(quote.data.proration_micros, quote.data.currency)}.`
                    : quote.data.proration_unavailable_reason ??
                      "The amount for the rest of this period is calculated by your payment provider."}
                </p>
              </div>
            ) : (
              <p role="alert" className="mt-2 text-xs text-destructive">
                {quote.isError
                  ? errorMessage(quote.error, "The seat price could not be loaded, so nothing can be bought right now.")
                  : "This plan has no seat price, so seats cannot be bought. Contact support."}
              </p>
            )
          ) : (
            <p className="mt-2 text-xs text-muted-foreground">
              {releasing && `Removes ${purchased - desired} unused ${seatsWord(purchased - desired)}. `}
              Your payment provider credits the unused part of this period on the next invoice.
            </p>
          )}

          {state.subscription && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              Current period ends {formatTimestampDate(state.subscription.current_period_end)}.
            </p>
          )}

          {change.isError && (
            <p role="alert" className="mt-2 text-xs text-destructive">
              {errorMessage(change.error, "The change didn't go through. Your seats are unchanged.")}
            </p>
          )}

          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setReviewing(false)}
              disabled={change.isPending}
              className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => change.mutate()}
              disabled={
                change.isPending ||
                (adding > 0 && (quote.isLoading || quote.data?.unit_price_micros === null || !quote.data))
              }
              className="fp-btn-primary inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
            >
              {change.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {adding > 0 ? `Buy ${adding} ${seatsWord(adding)}` : "Confirm"}
            </button>
          </div>
        </div>
      )}

      {paid && overBy > 0 && canManageBilling && !reconciling && (
        <button
          type="button"
          onClick={() => setReconciling(true)}
          className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-muted"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Bill the {overBy} extra {seatsWord(overBy)}
        </button>
      )}
      {reconciling && (
        <div className="mt-3 rounded-md border border-border bg-background p-3 text-xs">
          <p>
            Adds {overBy} {seatsWord(overBy)} to the subscription so everyone who holds one is paid for. Mid-period
            seats are charged pro rata by your payment provider.
          </p>
          {sync.isError && (
            <p role="alert" className="mt-2 text-destructive">
              {errorMessage(sync.error, "The change didn't go through. Your seats are unchanged.")}
            </p>
          )}
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" onClick={() => setReconciling(false)} className="rounded-md border border-border px-3 py-1.5 hover:bg-muted">
              Cancel
            </button>
            <button
              type="button"
              onClick={() => sync.mutate()}
              disabled={sync.isPending}
              className="fp-btn-primary rounded-md bg-primary px-3 py-1.5 font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
            >
              Confirm
            </button>
          </div>
        </div>
      )}

      {paid && !state.can_manage_seats && (
        <p className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground">
          Owners, admins and billing managers can add or remove seats.
        </p>
      )}

      {state.subscription && (
        <p className="mt-3 text-xs text-muted-foreground">
          {/* F-187: this read "Plan: enterprise · active · $ billing". */}
          {planName(state.subscription.quota_tier_key)} plan · {state.subscription.status.toLowerCase()}
          {state.subscription.cancel_at_period_end && " · cancels at the end of the period"}
          {" · billed in "}
          {currency.toUpperCase()}
          {state.delinquent_since && ` · payment outstanding since ${formatTimestampDate(state.delinquent_since)}`}
        </p>
      )}
    </section>
  );
};

export default SeatManager;
