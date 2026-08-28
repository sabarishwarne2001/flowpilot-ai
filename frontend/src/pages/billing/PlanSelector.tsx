import React, { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, Loader2 } from "lucide-react";

import { createCheckoutSession, getPlans } from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";
import { organizationBillingReturnPath } from "@/routes/tenantPaths";
import type { PlanOption } from "@/types/billing";

interface PlanSelectorProps {
  readonly organizationId: string;
  readonly organizationSlug: string;
  readonly canManageBilling: boolean;
  /** True when the org already has a subscription; changes copy and framing. */
  readonly hasSubscription: boolean;
  readonly currentSeats: number;
}

export const PlanSelector: React.FC<PlanSelectorProps> = ({
  organizationId,
  organizationSlug,
  canManageBilling,
  hasSubscription,
  currentSeats,
}) => {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [seats, setSeats] = useState<number>(Math.max(currentSeats, 1));
  const [confirming, setConfirming] = useState(false);

  const {
    data,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: billingKeys.plans(organizationId),
    queryFn: () => getPlans(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 5 * 60_000,
  });

  const plans = data?.plans ?? [];
  const selected = useMemo(
    () => plans.find((p) => p.key === selectedKey) ?? null,
    [plans, selectedKey],
  );

  const checkout = useMutation({
    mutationFn: (plan: PlanOption) => {
      const returnUrl =
        window.location.origin + organizationBillingReturnPath(organizationSlug);
      return createCheckoutSession(organizationId, {
        quota_tier_key: plan.key,
        seats,
        success_url: `${returnUrl}?outcome=success`,
        cancel_url: `${returnUrl}?outcome=cancelled`,
      });
    },
    onSuccess: (session) => {
      window.location.assign(session.url);
    },
  });

  if (!canManageBilling) {
    return null;
  }

  if (isLoading) {
    return (
      <section className="rounded-lg border border-border p-4">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading plans…
        </div>
      </section>
    );
  }

  if (isError) {
    return (
      <section className="rounded-lg border border-border p-4">
        <p role="alert" className="text-sm text-destructive">
          Plans couldn&apos;t be loaded.
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="mt-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Try again
        </button>
      </section>
    );
  }

  if (plans.length === 0) {
    return (
      <section className="rounded-lg border border-border p-4">
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
          <div>
            <p className="text-sm font-medium">No plans are available yet</p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Nothing is published for this organization to subscribe to. If you
              expected plans here, contact support.
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border border-border">
      <header className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold">
          {hasSubscription ? "Change plan" : "Choose a plan"}
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {hasSubscription
            ? "Switching plans starts a new checkout. Your current plan stays active until it completes."
            : "Pick a plan to start your subscription."}
        </p>
      </header>

      <ul className="divide-y divide-border">
        {plans.map((plan) => {
          const isSelected = plan.key === selectedKey;
          return (
            <li key={plan.key}>
              <label
                className={`flex cursor-pointer items-start gap-3 p-4 transition-colors ${
                  isSelected ? "bg-muted/50" : "hover:bg-muted/30"
                }`}
              >
                <input
                  type="radio"
                  name="plan"
                  value={plan.key}
                  checked={isSelected}
                  onChange={() => {
                    setSelectedKey(plan.key);
                    setConfirming(false);
                  }}
                  disabled={plan.is_current}
                  className="mt-1"
                />

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-sm font-medium">
                      {plan.display_name}
                    </span>
                    {plan.is_current && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">
                        <Check className="h-3 w-3" />
                        Current plan
                      </span>
                    )}
                  </div>

                  <p className="mt-1 text-sm text-muted-foreground">
                    {formatPrice(plan)}
                  </p>

                  {plan.notes && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {plan.notes}
                    </p>
                  )}

                  {plan.entitlements.length > 0 && (
                    <ul className="mt-2 space-y-0.5">
                      {plan.entitlements.slice(0, 4).map((e) => (
                        <li
                          key={e.event_type}
                          className="text-xs text-muted-foreground"
                        >
                          {e.event_type}:{" "}
                          {e.limit_quantity === null
                            ? "unlimited"
                            : `${e.limit_quantity.toLocaleString()} per ${e.period.toLowerCase()}`}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </label>
            </li>
          );
        })}
      </ul>

      {selected && !selected.is_current && (
        <div className="space-y-3 border-t border-border p-4">
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor="seat-count" className="text-sm">
              Seats
            </label>
            <input
              id="seat-count"
              type="number"
              min={1}
              max={10000}
              value={seats}
              onChange={(event) => {
                const next = Number.parseInt(event.target.value, 10);
                setSeats(Number.isNaN(next) ? 1 : Math.min(Math.max(next, 1), 10000));
                setConfirming(false);
              }}
              className="w-24 rounded-md border border-border px-2 py-1 text-sm"
            />
            <span className="text-xs text-muted-foreground">
              You&apos;ll see the total on the next screen, before you pay.
            </span>
          </div>

          {checkout.isError && (
            <p role="alert" className="text-sm text-destructive">
              Checkout couldn&apos;t be started. Your card was not charged.
              Please try again, or contact support if this continues.
            </p>
          )}

          {confirming ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm">
                Continue to payment for {selected.display_name}, {seats}{" "}
                {seats === 1 ? "seat" : "seats"}?
              </span>
              <button
                type="button"
                onClick={() => checkout.mutate(selected)}
                disabled={checkout.isPending}
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
              >
                {checkout.isPending && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                )}
                Continue to payment
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                disabled={checkout.isPending}
                className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-60"
              >
                Back
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              {hasSubscription ? "Change to this plan" : "Subscribe"}
            </button>
          )}
        </div>
      )}
    </section>
  );
};

function formatPrice(plan: PlanOption): string {
  if (plan.unit_amount === null || plan.currency === null) {
    return "Contact us for pricing";
  }
  const amount = new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: plan.currency.toUpperCase(),
  }).format(plan.unit_amount / 100);
  return plan.interval ? `${amount} per seat / ${plan.interval}` : `${amount} per seat`;
}

export default PlanSelector;
