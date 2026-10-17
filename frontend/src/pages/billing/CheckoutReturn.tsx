import React, { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";

import { getSubscriptionState } from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";
import { organizationBillingPath } from "@/routes/tenantPaths";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";

/** How long to wait for the payment's webhook before saying it is late (website and payments session, C13). */
export const CONFIRMATION_TIMEOUT_MS = 60_000;
const POLL_MS = 2_000;
const LIVE = new Set(["active", "trialing", "past_due"]);

/**
 * Where the payment page sends the customer back.
 *
 * Website and payments session (C13). The return only says the customer left the payment page;
 * the payment is confirmed when Dodo's webhook reaches Servabo, a moment later. So the page shows
 * "Confirming your payment…" and polls the subscription until it is live (the one Dodo named in
 * `subscription_id`, when it did), then shows the plan. If that takes longer than a minute it says
 * so plainly instead of spinning. Dodo appends `status` to the return address: a declined payment
 * (`status=failed`) is said at once. `outcome=cancelled` is the customer leaving the page.
 */
export const CheckoutReturn: React.FC = () => {
  const [params] = useSearchParams();
  const outcome = params.get("outcome");
  const gatewayStatus = (params.get("status") ?? "").toLowerCase();
  const expectedSubscription = params.get("subscription_id");
  const queryClient = useQueryClient();
  const { organization, organizationId } = useResolvedOrganization();
  const billingPath = organizationBillingPath(organization.organization_slug);

  const declined = gatewayStatus === "failed" || gatewayStatus === "cancelled";
  const waiting = outcome === "success" && !declined;
  const [timedOut, setTimedOut] = useState(false);

  const state = useQuery({
    queryKey: billingKeys.subscription(organizationId),
    queryFn: () => getSubscriptionState(organizationId),
    enabled: waiting,
    refetchInterval: (query) => {
      const sub = query.state.data?.subscription;
      const confirmed =
        sub !== null &&
        sub !== undefined &&
        LIVE.has(sub.status.toLowerCase()) &&
        (!expectedSubscription || sub.gateway_subscription_id === expectedSubscription || sub.stripe_subscription_id === expectedSubscription);
      return confirmed || timedOut ? false : POLL_MS;
    },
  });
  const sub = state.data?.subscription ?? null;
  const confirmed =
    sub !== null &&
    LIVE.has(sub.status.toLowerCase()) &&
    (!expectedSubscription || sub.gateway_subscription_id === expectedSubscription || sub.stripe_subscription_id === expectedSubscription);

  useEffect(() => {
    if (!waiting || confirmed) {return undefined;}
    const timer = window.setTimeout(() => setTimedOut(true), CONFIRMATION_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [waiting, confirmed]);

  useEffect(() => {
    if (confirmed || !waiting) {
      void queryClient.invalidateQueries({ queryKey: billingKeys.all(organizationId) });
    }
  }, [queryClient, organizationId, confirmed, waiting]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-xl p-4 sm:p-6">
        <div className="rounded-lg border border-border p-6 text-center" data-testid="checkout-return" aria-live="polite">
          {!waiting ? (
            <>
              <XCircle className="mx-auto h-10 w-10 text-muted-foreground" aria-hidden="true" />
              <h1 className="mt-3 text-lg font-semibold">{declined ? "The payment did not go through" : "Checkout cancelled"}</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">
                {declined
                  ? "Your card was not charged and nothing has changed. Try another card, or pick a plan again whenever you're ready."
                  : "No payment was taken and nothing has changed. You can pick a plan again whenever you're ready."}
              </p>
            </>
          ) : confirmed ? (
            <>
              <CheckCircle2 className="mx-auto h-10 w-10 text-primary" aria-hidden="true" />
              <h1 className="mt-3 text-lg font-semibold">Payment confirmed</h1>
              <p className="mt-1.5 text-sm text-muted-foreground" data-testid="checkout-confirmed-plan">
                You&apos;re on the {state.data?.plan_key ?? sub?.quota_tier_key} plan with {state.data?.seats_purchased}{" "}
                {state.data?.seats_purchased === 1 ? "seat" : "seats"}. The receipt is under Billing → Invoices.
              </p>
            </>
          ) : timedOut ? (
            <>
              <Clock className="mx-auto h-10 w-10 text-muted-foreground" aria-hidden="true" />
              <h1 className="mt-3 text-lg font-semibold">Your payment is taking longer to confirm</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">
                Dodo Payments has your payment, but its confirmation has not reached us yet. That usually takes under
                a minute. Your plan changes on its own when it arrives; there is no need to pay again. If nothing has
                changed in ten minutes, contact support with the time you paid.
              </p>
            </>
          ) : (
            <>
              <Loader2 className="mx-auto h-10 w-10 animate-spin text-primary" aria-hidden="true" />
              <h1 className="mt-3 text-lg font-semibold">Confirming your payment…</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">This usually takes a few seconds. Please keep this page open.</p>
            </>
          )}

          <Link
            to={billingPath}
            className="fp-btn-primary mt-5 inline-block rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Back to billing
          </Link>
        </div>
      </div>
    </div>
  );
};

export default CheckoutReturn;
