/**
 * Campaign session 4 (CAMPAIGN-MAP 4.3) — the public pricing page.
 *
 * Nothing on this page is typed by hand: the plans, their prices, what each includes and
 * the trial length come from `GET /api/v1/pricing`, which the server builds from the
 * published plans with the same helpers as the in-app plan cards (TENANCY-AND-PLANS.md is
 * generated from the same seed). Only the WORDS are here: allowance and feature labels are
 * the in-app cards' own (`planEntitlements`, `planFeatures`), so a new capability without a
 * label is a compile error rather than a schema key on a public page.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, Check, Loader2 } from "lucide-react";

import { LoadFailed } from "@/components/common/LoadFailed";
import { PublicPageShell } from "@/components/public/PublicPageShell";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, HINT, SECTION_TITLE } from "@/components/ui/primitives";
import { CORE_FEATURES, PLAN_FEATURE_LABELS, PLAN_FEATURE_ORDER, isPlanFeatureKey } from "@/constants/planFeatures";
import { ROUTES } from "@/constants/routes";
import { getPublicPricing, publicPricingKeys } from "@/services/api/publicPricing";
import { useAuthStore } from "@/store/useAuthStore";
import { KNOWN_METERS, describeEntitlement, meterLabel, type MeterKey } from "@/types/planEntitlements";
import type { PublicPlan, PublicPlanAllowance, PublicPlanPrice, PublicPricingResponse } from "@/types/publicPricing";
import { formatMoney } from "@/utils/displayTime";

const LINK = "font-medium text-primary underline-offset-4 hover:underline dark:text-[hsl(var(--primary-on-dark))]";

/** "$49", "$12.50": whole amounts without cents. */
function formatPlanPrice(price: PublicPlanPrice): string {
  const whole = price.unit_amount % 100 === 0;
  return formatMoney(
    price.unit_amount / 100,
    price.currency,
    whole ? { minimumFractionDigits: 0, maximumFractionDigits: 0 } : {},
  );
}

/** The price a plan is listed at: monthly, in US dollars when it is sold in them. */
function listedPrice(plan: PublicPlan): PublicPlanPrice | null {
  const monthly = plan.prices.filter((price) => price.interval === "month");
  return monthly.find((price) => price.currency === "USD") ?? monthly[0] ?? plan.prices[0] ?? null;
}

function yearlyPrice(plan: PublicPlan, listed: PublicPlanPrice | null): PublicPlanPrice | null {
  if (!listed || listed.interval !== "month") {
    return null;
  }
  return plan.prices.find((price) => price.interval === "year" && price.currency === listed.currency) ?? null;
}

const OVERAGE_HINTS: Readonly<Record<string, string>> = {
  ALLOW_AND_BILL: "overage billed",
  ALLOW_AND_WARN: "soft limit, no overage charge",
  REFUSE: "hard limit",
};

/** One allowance in words, as the plan card says it; null when it has no line. */
function allowanceText(allowance: PublicPlanAllowance, perSeat: boolean): string | null {
  const known = Object.prototype.hasOwnProperty.call(KNOWN_METERS, allowance.key)
    ? KNOWN_METERS[allowance.key as MeterKey]
    : undefined;
  if (allowance.limit_quantity === null && !known?.capability) {
    // TENANCY-AND-PLANS.md: an absent workspace limit is "unlimited", an absent meter "no ceiling".
    if (allowance.key === "limit.workspaces") {
      return "Unlimited workspaces";
    }
    const label = meterLabel(allowance.key);
    return `No monthly ceiling on ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
  }
  const line = describeEntitlement(allowance.key, allowance.limit_quantity, allowance.period, {
    perSeat: perSeat && !allowance.key.startsWith("limit."),
  });
  return line?.text ?? null;
}

function overageHint(allowance: PublicPlanAllowance): string | null {
  if (allowance.limit_quantity === null || allowance.key.startsWith("limit.") || !allowance.overage_policy) {
    return null;
  }
  return OVERAGE_HINTS[allowance.overage_policy.toUpperCase()] ?? null;
}

const featuresOf = (plan: PublicPlan | null): ReadonlySet<string> =>
  new Set((plan?.features ?? []).filter(isPlanFeatureKey));

const PlanCard: React.FC<{
  readonly plan: PublicPlan;
  readonly previous: PublicPlan | null;
  readonly trialPlanKey: string;
  readonly trialDays: number;
  readonly signedIn: boolean;
}> = ({ plan, previous, trialPlanKey, trialDays, signedIn }) => {
  const listed = listedPrice(plan);
  const yearly = yearlyPrice(plan, listed);
  const isTrialPlan = plan.key === trialPlanKey;
  const own = featuresOf(plan);
  const inherited = featuresOf(previous);
  const added = PLAN_FEATURE_ORDER.filter((key) => own.has(key) && !inherited.has(key));
  const headingId = `plan-${plan.key}-name`;

  return (
    <li className="fp-card flex min-w-0 flex-col p-5" data-testid={`pricing-plan-${plan.key}`} aria-labelledby={headingId}>
      <h2 id={headingId} className={SECTION_TITLE}>
        {plan.display_name}
      </h2>

      <div className="mt-3" data-testid={`pricing-price-${plan.key}`}
           data-amount={listed?.unit_amount ?? ""} data-currency={listed?.currency ?? ""}
           data-interval={listed?.interval ?? ""}>
        {listed ? (
          <p className="flex flex-wrap items-baseline gap-x-1.5">
            <span className="text-3xl font-semibold tracking-tight text-foreground">{formatPlanPrice(listed)}</span>
            <span className="text-sm text-muted-foreground">
              {isTrialPlan && listed.unit_amount === 0
                ? trialDays > 0
                  ? `for ${trialDays} days`
                  : null
                : `per seat / ${listed.interval}`}
            </span>
          </p>
        ) : (
          <p className="text-sm font-medium text-muted-foreground">Price not published yet</p>
        )}
        {yearly ? (
          <p className="mt-1 text-xs text-muted-foreground" data-testid={`pricing-yearly-${plan.key}`}>
            or {formatPlanPrice(yearly)} per seat / year
          </p>
        ) : null}
      </div>

      <p className={`${HINT} mt-2`}>
        {isTrialPlan
          ? trialDays > 0
            ? `A ${trialDays}-day trial with these allowances, then choose a paid plan.`
            : "These allowances apply while you evaluate the product."
          : plan.per_seat
            ? "Allowances are per seat, shared across your organization."
            : "Allowances are per organization."}
      </p>

      <Link
        to={signedIn ? ROUTES.DASHBOARD : ROUTES.REGISTER}
        className={`${isTrialPlan ? BUTTON_PRIMARY : BUTTON_SECONDARY} mt-4 w-full`}
        data-testid={`pricing-cta-${plan.key}`}
      >
        {signedIn ? "Open the app" : isTrialPlan ? "Start free trial" : "Sign up"}
        {isTrialPlan ? <ArrowRight className="h-4 w-4" aria-hidden="true" /> : null}
      </Link>

      <ul className="mt-5 space-y-1.5" aria-label={`${plan.display_name} allowances`}>
        {plan.allowances.map((allowance) => {
          const text = allowanceText(allowance, plan.per_seat);
          if (!text) {
            return null;
          }
          const hint = overageHint(allowance);
          return (
            <li key={allowance.key} data-allowance={allowance.key} className="text-sm leading-snug text-foreground [overflow-wrap:anywhere]">
              {text}
              {hint ? <span className="text-muted-foreground"> · {hint}</span> : null}
            </li>
          );
        })}
      </ul>

      <ul className="mt-4 space-y-1.5 border-t border-border/70 pt-4" aria-label={`${plan.display_name} features`}>
        {previous === null ? (
          CORE_FEATURES.map((text) => (
            <li key={text} className="flex items-start gap-2 text-sm text-foreground">
              <Check className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" aria-hidden="true" />
              <span className="min-w-0 [overflow-wrap:anywhere]">{text}</span>
            </li>
          ))
        ) : (
          <li className="text-sm font-medium text-foreground">Everything in {previous.display_name}, plus:</li>
        )}
        {added.map((key) => (
          <li key={key} data-feature={key} className="flex items-start gap-2 text-sm text-foreground">
            <Check className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" aria-hidden="true" />
            <span className="min-w-0 [overflow-wrap:anywhere]">{PLAN_FEATURE_LABELS[key]}</span>
          </li>
        ))}
      </ul>
    </li>
  );
};

const TrialFacts: React.FC<{ readonly pricing: PublicPricingResponse }> = ({ pricing }) => {
  const { days, plan_key: planKey } = pricing.trial;
  if (days <= 0) {
    return null;
  }
  const planName = pricing.plans.find((plan) => plan.key === planKey)?.display_name ?? "Free";
  return (
    <section aria-labelledby="trial-heading" className="fp-card p-5 sm:p-6" data-testid="pricing-trial">
      <h2 id="trial-heading" className={SECTION_TITLE}>
        How the free trial works
      </h2>
      <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-relaxed text-foreground">
        <li>
          Every account gets one {days}-day trial on the {planName} plan. It starts when you create your first
          organization; another organization or a new workspace does not restart it.
        </li>
        <li>During the trial, the {planName} plan&apos;s allowances apply. No payment details are needed to start.</li>
        <li>
          When the trial ends, the organization becomes read-only until someone subscribes to a paid plan: you can
          still sign in, read, search and export your documents. Subscribing lifts this at once.
        </li>
        <li>Paid plans are chosen in the app, under Billing, after you sign up.</li>
      </ul>
    </section>
  );
};

const Pricing: React.FC = () => {
  const signedIn = useAuthStore((state) => state.isAuthenticated);
  const query = useQuery({
    queryKey: publicPricingKeys.list,
    queryFn: getPublicPricing,
    staleTime: 5 * 60_000,
    retry: 1,
    refetchOnWindowFocus: false,
  });
  const pricing = query.data ?? null;
  const days = pricing?.trial.days ?? 0;

  return (
    <PublicPageShell title="Pricing">
      <div className="mx-auto w-full max-w-6xl space-y-8 px-4 py-10 sm:px-6 lg:px-8">
        <header className="max-w-2xl space-y-3">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">Plans and pricing</h1>
          <p className="text-sm leading-relaxed text-muted-foreground sm:text-base">
            Paid plans are priced per seat, per month, and every person in your organization uses one seat.
            {days > 0 ? ` Start with a ${days}-day free trial.` : null}
          </p>
          {!signedIn ? (
            <div className="flex flex-wrap gap-2 pt-1">
              <Link to={ROUTES.REGISTER} className={BUTTON_PRIMARY} data-testid="pricing-cta-hero">
                Start free trial
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
              <Link to={ROUTES.LOGIN} className={BUTTON_SECONDARY}>
                Sign in
              </Link>
            </div>
          ) : null}
        </header>

        {query.isLoading ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading plans…
          </p>
        ) : query.isError || !pricing ? (
          <LoadFailed what="Plans" onRetry={() => void query.refetch()} />
        ) : pricing.plans.length === 0 ? (
          <p className="text-sm text-muted-foreground" role="status">
            No plans are published yet.
          </p>
        ) : (
          <>
            <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Plans">
              {pricing.plans.map((plan, index) => (
                <PlanCard
                  key={plan.key}
                  plan={plan}
                  previous={index > 0 ? (pricing.plans[index - 1] ?? null) : null}
                  trialPlanKey={pricing.trial.plan_key}
                  trialDays={days}
                  signedIn={signedIn}
                />
              ))}
            </ul>
            <TrialFacts pricing={pricing} />
          </>
        )}

        <div className={`${HINT} max-w-3xl space-y-1.5`}>
          <p>
            A paid plan&apos;s monthly allowances are per seat and shared by everyone in the organization. AI token and
            spending ceilings sit above them as a safety net; after you sign up, the plan page in the app lists every
            allowance.
          </p>
          <p>
            Read the{" "}
            <Link to={ROUTES.TERMS} className={LINK}>
              terms of service
            </Link>{" "}
            and the{" "}
            <Link to={ROUTES.PRIVACY} className={LINK}>
              privacy policy
            </Link>
            .
          </p>
        </div>
      </div>
    </PublicPageShell>
  );
};

export default Pricing;
