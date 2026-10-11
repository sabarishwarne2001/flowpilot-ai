import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Info, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { createCheckoutSession, createPortalSession, getPlans } from "@/services/api/billing";
// ARCH50-S2:plan-selector-interval — annual billing, INR and promo codes.
import { quotePromoCode } from "@/services/api/revops";
import { REVOPS_MESSAGES, money, type PromoQuote, type RevOpsCode } from "@/types/revops";
import { ApiError } from "@/services/api/client";
import { billingKeys, entitlementKeys } from "@/services/api/queryKeys";
import { organizationBillingReturnPath } from "@/routes/tenantPaths";
import type { PlanOption, PlanPriceOption } from "@/types/billing";
import { describeEntitlement, entitlementRank } from "@/types/planEntitlements";
import {
  CORE_FEATURES,
  PLAN_FEATURE_LABELS,
  PLAN_FEATURE_ORDER,
  TIER_RANK,
  isPlanFeatureKey,
} from "@/constants/planFeatures";

interface PlanSelectorProps {
  readonly organizationId: string;
  readonly organizationSlug: string;
  readonly canManageBilling: boolean;
  readonly hasSubscription: boolean;
  readonly currentSeats: number;
  /** Campaign session 1: members plus pending invitations; checkout cannot sell fewer. */
  readonly minimumSeats?: number;
}

const isFreePlan = (plan: PlanOption): boolean => plan.is_priced && plan.unit_amount === 0;

/**
 * Plan picker.
 *
 * ARCH39-S1:free-plan-selector — what ARCH-39 changed
 * ===================================================
 *
 * Free is assigned, not bought. Choosing it calls the same endpoint, which
 * now assigns the tier without touching a payment gateway and answers
 * `kind: "assigned"`; this component refreshes in place instead of
 * redirecting. With a live paid subscription the server refuses (409
 * PAID_SUBSCRIPTION_ACTIVE) and the message says to cancel in the portal.
 *
 * When the deployment has no gateway credentials the plans response says so
 * (`checkout_available: false`), paid plans explain why they cannot be
 * bought, and Free still works.
 *
 * The current plan is badged from `current_tier_key`, which the server
 * resolves from the live subscription or, without one, from the
 * organization's assigned tier.
 */
export const PlanSelector: React.FC<PlanSelectorProps> = ({
  organizationId,
  organizationSlug,
  canManageBilling,
  hasSubscription,
  currentSeats,
  minimumSeats = 1,
}) => {
  const queryClient = useQueryClient();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const floor = Math.max(1, minimumSeats);
  const [seats, setSeats] = useState<number>(Math.max(currentSeats, floor));
  const [confirming, setConfirming] = useState(false);
  const [interval, setBillingInterval] = useState<"month" | "year">("month");
  const [currency, setCurrency] = useState<"USD" | "INR">("USD");
  const [promoCode, setPromoCode] = useState("");
  const [promoQuote, setPromoQuote] = useState<PromoQuote | null>(null);
  const [promoError, setPromoError] = useState<string | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: billingKeys.plans(organizationId),
    queryFn: () => getPlans(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 5 * 60_000,
  });

  const plans = data?.plans ?? [];
  const checkoutAvailable = data?.checkout_available !== false;
  const currentKey = data?.current_tier_key ?? null;
  const currentPlan = plans.find((plan) => plan.key === currentKey) ?? null;

  const selected = useMemo(
    () => plans.find((plan) => plan.key === selectedKey) ?? null,
    [plans, selectedKey],
  );

  const hasPriceBooks = plans.some((plan) => (plan.prices ?? []).length > 0);
  const currencies = Array.from(new Set(plans.flatMap((plan) => (plan.prices ?? []).map((p) => p.currency))));
  const priceFor = (plan: PlanOption): PlanPriceOption | null =>
    (plan.prices ?? []).find((p) => p.interval === interval && p.currency === currency) ?? null;

  const applyPromo = useMutation({
    mutationFn: (plan: PlanOption) => quotePromoCode(organizationId, {
      code: promoCode.trim(), quota_tier_key: plan.key, interval, currency, seats,
    }),
    onSuccess: (quote) => {
      setPromoQuote(quote);
      setPromoError(null);
    },
    onError: (error) => {
      setPromoQuote(null);
      const code = error instanceof ApiError ? error.code : undefined;
      setPromoError((code && REVOPS_MESSAGES[code as RevOpsCode]) || "That promo code can't be used.");
    },
  });

  // Campaign session 1 (F-253): a subscriber changes plans on the live subscription, in
  // the portal; a second checkout would create (and charge) a second subscription.
  const portal = useMutation({
    mutationFn: () => createPortalSession(organizationId, { return_url: window.location.href }),
    onSuccess: (session) => {
      window.location.assign(session.url);
    },
  });

  const checkout = useMutation({
    mutationFn: (plan: PlanOption) => {
      const returnUrl =
        window.location.origin + organizationBillingReturnPath(organizationSlug);
      const priced = hasPriceBooks && priceFor(plan) !== null;
      return createCheckoutSession(organizationId, {
        quota_tier_key: plan.key,
        seats,
        success_url: `${returnUrl}?outcome=success`,
        cancel_url: `${returnUrl}?outcome=cancelled`,
        ...(priced ? { interval, currency } : {}),
        // A code the gateway cannot honour (no gateway coupon) is never sent to checkout.
        ...(promoQuote?.online ? { promo_code: promoQuote.code } : {}),
      });
    },
    onSuccess: async (session, plan) => {
      if (session.kind === "assigned") {
        setSelectedKey(null);
        setConfirming(false);
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: billingKeys.all(organizationId) }),
          queryClient.invalidateQueries({ queryKey: entitlementKeys.all(organizationId) }),
        ]);
        toast.success(`${plan.display_name} is now your plan.`);
        return;
      }
      window.location.assign(session.url);
    },
  });

  const checkoutError =
    checkout.error instanceof ApiError
      ? checkout.error.message
      : checkout.error
        ? "The plan change couldn't be started. Please try again."
        : null;

  if (!canManageBilling) {
    return null;
  }

  if (isLoading) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading plans…
        </div>
      </section>
    );
  }

  if (isError) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
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
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
          <div>
            <p className="text-sm font-medium text-foreground">No plans are available yet</p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Nothing is published for this organization to subscribe to. If you
              expected plans here, contact support.
            </p>
          </div>
        </div>
      </section>
    );
  }

  const selectedIsFree = selected ? isFreePlan(selected) : false;
  const hasPaidPlans = plans.some((plan) => !isFreePlan(plan));
  const selectedBlocked = selected !== null && !selectedIsFree && !checkoutAvailable;

  return (
    <section id="plans" className="scroll-mt-4 rounded-lg border border-border bg-card">
      <header className="border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-foreground">
            {hasSubscription ? "Change plan" : "Choose a plan"}
          </h2>
          {currentPlan ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-semibold text-primary">
              <Check className="h-3 w-3" aria-hidden />
              You&apos;re on {currentPlan.display_name}
            </span>
          ) : (
            <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground">
              No plan assigned yet
            </span>
          )}
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {hasSubscription
            ? "Plans are changed in the billing portal: it moves this subscription and prorates the difference, so nothing is charged twice. To move to Free, cancel there."
            : "Free starts immediately. Paid plans open a secure checkout."}
        </p>
        {hasPriceBooks && (
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs" data-testid="plan-billing-options">
            <div className="inline-flex rounded-md border border-border" role="group" aria-label="Billing interval">
              {(["month", "year"] as const).map((value) => (
                <button key={value} type="button" aria-pressed={interval === value}
                        onClick={() => { setBillingInterval(value); setPromoQuote(null); setConfirming(false); }}
                        className={`px-2.5 py-1 ${interval === value ? "bg-primary text-primary-foreground" : "text-foreground hover:bg-muted"}`}>
                  {value === "month" ? "Monthly" : "Annual"}
                </button>
              ))}
            </div>
            {currencies.length > 1 && (
              <label className="inline-flex items-center gap-1.5">
                <span className="text-muted-foreground">Currency</span>
                <select value={currency} onChange={(e) => { setCurrency(e.target.value as "USD" | "INR"); setPromoQuote(null); setConfirming(false); }}
                        className="rounded-md border border-border bg-background px-2 py-1 text-foreground">
                  {currencies.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
            )}
          </div>
        )}
        {!checkoutAvailable && (
          <p className="mt-2 flex items-start gap-1.5 rounded-md bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-800 dark:text-amber-300">
            <Info className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden />
            <span>
              {data?.checkout_unavailable_reason ??
                "Paid checkout is not available in this environment."}
            </span>
          </p>
        )}
      </header>

      {/* N-020 item 3: the promo code is part of the plan picker, visible before a plan is chosen.
          The quote is priced by the server for the chosen plan, interval, currency and seats, and the
          code travels with the checkout, which reserves it. */}
      {hasPaidPlans && (
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3" data-testid="promo-code">
          <label htmlFor="promo-code-input" className="text-sm font-medium text-foreground">
            Promo code
          </label>
          <input
            id="promo-code-input"
            value={promoCode}
            maxLength={32}
            autoComplete="off"
            placeholder="e.g. LAUNCH20"
            onChange={(event) => {
              setPromoCode(event.target.value.toUpperCase());
              setPromoQuote(null);
              setPromoError(null);
            }}
            className="w-40 rounded-md border border-border bg-background px-3 py-1.5 text-sm uppercase text-foreground placeholder:normal-case placeholder:text-muted-foreground"
          />
          <button
            type="button"
            disabled={promoCode.trim().length < 3 || applyPromo.isPending || !selected || isFreePlan(selected)}
            onClick={() => selected && applyPromo.mutate(selected)}
            className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-60"
          >
            {applyPromo.isPending ? "Checking…" : "Apply"}
          </button>
          {!selected || isFreePlan(selected) ? (
            <span className="text-xs text-muted-foreground">Choose a paid plan below, then apply your code.</span>
          ) : null}
          {promoQuote && promoQuote.online && (
            <span className="text-xs text-emerald-700 dark:text-emerald-400" role="status">
              {`${promoQuote.code}: −${money(promoQuote.discount_micros, currency)} per ${interval} ` +
                `(${money(promoQuote.final_amount_micros, currency)} instead of ` +
                `${money(promoQuote.list_amount_micros, currency)}) ` +
                (promoQuote.duration === "REPEATING"
                  ? `for ${promoQuote.duration_in_months ?? 0} months`
                  : promoQuote.duration === "FOREVER"
                    ? "while subscribed"
                    : "on the first payment")}
            </span>
          )}
          {promoQuote && !promoQuote.online && (
            <span className="text-xs text-amber-700 dark:text-amber-400" role="status">
              {REVOPS_MESSAGES.PROMO_NOT_AVAILABLE_ONLINE}
            </span>
          )}
          {promoError && (
            <span role="alert" className="text-xs text-destructive">
              {promoError}
            </span>
          )}
        </div>
      )}

      <ul className="divide-y divide-border">
        {plans.map((plan, index) => {
          const isSelected = plan.key === selectedKey;
          const isCurrent = plan.is_current || plan.key === currentKey;
          return (
            <li key={plan.key}>
              <label
                className={`flex cursor-pointer items-start gap-3 p-4 transition-colors ${
                  isCurrent
                    ? "bg-primary/5"
                    : isSelected
                      ? "bg-muted/40"
                      : "hover:bg-muted/20"
                } ${isCurrent ? "cursor-default" : ""}`}
              >
                <input
                  type="radio"
                  name="plan"
                  value={plan.key}
                  checked={isSelected}
                  onChange={() => {
                    setSelectedKey(plan.key);
                    setConfirming(false);
                    setPromoQuote(null);
                    setPromoError(null);
                    checkout.reset();
                  }}
                  disabled={isCurrent}
                  className="mt-1"
                />

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-sm font-semibold text-foreground">
                      {plan.display_name}
                    </span>
                    {isCurrent && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-primary-foreground">
                        <Check className="h-3 w-3" aria-hidden />
                        Current plan
                      </span>
                    )}
                  </div>

                  <p className="mt-1 text-sm font-medium text-muted-foreground">
                    {hasPriceBooks && !isFreePlan(plan) ? formatBookPrice(priceFor(plan), interval, currency) : formatPrice(plan)}
                  </p>

                  {plan.notes && (
                    <p className="mt-1 text-xs text-muted-foreground">{plan.notes}</p>
                  )}

                  <PlanFeatureList plan={plan} previous={index > 0 ? (plans[index - 1] ?? null) : null} />

                  {/* ARCH50-S2:plan-cta-book-priced — a plan sold through a price book is purchasable even when its
                      tier version carries no price of its own (found by the ARCH-50 browser smoke). */}
                  {!isCurrent && !isFreePlan(plan) && (hasPriceBooks ? Boolean(priceFor(plan)?.price_id) : plan.is_priced) ? (
                    <button
                      type="button"
                      data-testid={`plan-cta-${plan.key}`}
                      disabled={hasSubscription ? !canManageBilling || portal.isPending : !checkoutAvailable}
                      onClick={(event) => {
                        event.preventDefault();
                        if (hasSubscription) {
                          portal.mutate();
                          return;
                        }
                        if (plan.key !== selectedKey) {
                          setPromoQuote(null);
                          setPromoError(null);
                        }
                        setSelectedKey(plan.key);
                        setConfirming(true);
                        checkout.reset();
                      }}
                      className="fp-btn-primary mt-3 inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
                    >
                      {(TIER_RANK[plan.key] ?? 0) > (TIER_RANK[currentKey ?? "free"] ?? 0)
                        ? `Upgrade to ${plan.display_name}`
                        : `Switch to ${plan.display_name}`}
                      {hasSubscription ? " in the billing portal" : null}
                    </button>
                  ) : null}
                </div>
              </label>
            </li>
          );
        })}
      </ul>

      {selected && !(selected.is_current || selected.key === currentKey) && (
        <div className="space-y-3 border-t border-border bg-muted/10 p-4">
          {!selectedIsFree && (
            <div className="flex flex-wrap items-center gap-3">
              <label htmlFor="seat-count" className="text-sm font-medium text-foreground">
                Seats:
              </label>
              <input
                id="seat-count"
                type="number"
                min={floor}
                max={10000}
                value={seats}
                onChange={(event) => {
                  const next = Number.parseInt(event.target.value, 10);
                  setSeats(Number.isNaN(next) ? floor : Math.min(Math.max(next, floor), 10000));
                  setConfirming(false);
                }}
                className="w-24 rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              />
              <span className="text-xs text-muted-foreground">
                {floor > 1 ? `At least ${floor}: everyone in the organization and pending invitations hold a seat. ` : ""}
                You&apos;ll see the total at checkout, before you pay.
              </span>
            </div>
          )}

          {checkoutError && (
            <p role="alert" className="text-sm text-destructive">
              {checkoutError}
            </p>
          )}

          {selectedBlocked ? (
            <p className="text-sm text-muted-foreground">
              This plan can&apos;t be purchased here until paid checkout is configured.
            </p>
          ) : confirming ? (
            <div className="flex flex-wrap items-center gap-2 pt-2">
              <span className="text-sm text-foreground">
                {selectedIsFree ? (
                  <>
                    Switch to <strong>{selected.display_name}</strong> now?
                  </>
                ) : (
                  <>
                    Continue to payment for <strong>{selected.display_name}</strong> ({seats}{" "}
                    {seats === 1 ? "seat" : "seats"}){promoQuote?.online ? <> with promo code <strong>{promoQuote.code}</strong></> : null}?
                  </>
                )}
              </span>
              <button
                type="button"
                onClick={() => checkout.mutate(selected)}
                disabled={checkout.isPending}
                className="fp-btn-primary inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-60"
              >
                {checkout.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {selectedIsFree ? "Switch to Free" : "Continue to payment"}
              </button>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                disabled={checkout.isPending}
                className="rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground hover:bg-muted disabled:opacity-60"
              >
                Back
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="fp-btn-primary rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              {selectedIsFree
                ? "Use this plan"
                : hasSubscription
                  ? "Change to this plan"
                  : "Subscribe"}
            </button>
          )}
        </div>
      )}
    </section>
  );
};

/**
 * HM-S1:plan-features — what a plan adds over the one below it.
 *
 * Read from the plan's own published entitlements: a `capability.*` or
 * `addon.*` row on the tier IS the grant, so the card lists exactly what the
 * request path will allow. Quantities (pages, tokens, storage) follow in a
 * quieter list.
 */
const featuresOf = (plan: PlanOption): ReadonlySet<string> =>
  new Set(plan.entitlements.map((entry) => entry.event_type).filter(isPlanFeatureKey));

const PlanFeatureList: React.FC<{ readonly plan: PlanOption; readonly previous: PlanOption | null }> = ({
  plan,
  previous,
}) => {
  const own = featuresOf(plan);
  const inherited = previous ? featuresOf(previous) : new Set<string>();
  const added = PLAN_FEATURE_ORDER.filter((key) => own.has(key) && !inherited.has(key));
  // Campaign session 1: a paid plan's metered allowances are per seat; the counts a
  // customer plans by come first, and every allowance is listed (they are promises).
  const perSeat = (plan.unit_amount ?? 0) > 0;
  const meters = [...plan.entitlements]
    .filter((entry) => !isPlanFeatureKey(entry.event_type))
    .sort((a, b) => entitlementRank(a.event_type) - entitlementRank(b.event_type))
    .map((entry) =>
      describeEntitlement(entry.event_type, entry.limit_quantity, entry.period, {
        perSeat: perSeat && !entry.event_type.startsWith("limit."),
      }),
    )
    .filter((line): line is NonNullable<typeof line> => line !== null);

  return (
    <div className="mt-2 space-y-2">
      <ul className="space-y-1" aria-label={`${plan.display_name} features`}>
        {previous === null ? (
          CORE_FEATURES.map((text) => (
            <li key={text} className="flex items-start gap-1.5 text-xs text-foreground">
              <Check className="mt-0.5 h-3 w-3 flex-shrink-0 text-primary" aria-hidden />
              <span>{text}</span>
            </li>
          ))
        ) : (
          <li className="text-xs font-medium text-foreground">
            Everything in {previous.display_name}, plus:
          </li>
        )}
        {added.map((key) => (
          <li key={key} data-feature={key} className="flex items-start gap-1.5 text-xs text-foreground">
            <Check className="mt-0.5 h-3 w-3 flex-shrink-0 text-primary" aria-hidden />
            <span>{PLAN_FEATURE_LABELS[key]}</span>
          </li>
        ))}
      </ul>
      {meters.length > 0 ? (
        <ul className="space-y-0.5" aria-label={`${plan.display_name} allowances`}>
          {meters.map((line) => (
            <li key={line.key} className="text-[11px] text-muted-foreground">
              {line.text}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
};

/**
 * ARCH-29 Tranche 2 / HM-S1. Zero is a price: `unit_amount === 0` renders
 * "Free", which a truthiness test would get wrong. Every plan on sale carries
 * a published price, Enterprise included; a tier without one is a deployment
 * that has not configured its gateway product yet, and says exactly that.
 */
/** ARCH50-S2:plan-prices. A plan's price for the chosen interval and currency from the plan price books. */
function formatBookPrice(price: PlanPriceOption | null, interval: string, currency: string): string {
  if (price === null) {
    return `Not sold ${interval === "year" ? "annually" : "monthly"} in ${currency} — contact sales`;
  }
  const amount = new Intl.NumberFormat(undefined, { style: "currency", currency: price.currency.toUpperCase() })
    .format(price.unit_amount / 100);
  const suffix = price.price_id ? "" : " (contact sales)";
  return `${amount} per seat / ${price.interval}${suffix}`;
}

function formatPrice(plan: PlanOption): string {
  if (!plan.is_priced || plan.unit_amount === null || plan.currency === null) {
    return "Price not configured yet";
  }

  if (plan.unit_amount === 0) {
    return "Free";
  }

  const amount = new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: plan.currency.toUpperCase(),
  }).format(plan.unit_amount / 100);
  return plan.interval ? `${amount} per seat / ${plan.interval}` : `${amount} per seat`;
}

export default PlanSelector;
