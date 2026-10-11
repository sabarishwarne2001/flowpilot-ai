import React, { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CreditCard, ExternalLink, Loader2 } from "lucide-react";

import ConsumptionDashboard from "@/pages/billing/ConsumptionDashboard";
import InvoiceBrowser from "@/pages/billing/InvoiceBrowser";
import PlanSelector from "@/pages/billing/PlanSelector";
// ARCH50-S2:billing-contract
import InvoicedContractPanel from "@/components/billing/InvoicedContractPanel";
import SeatManager from "@/pages/billing/SeatManager";
import SpendLimitForm from "@/pages/billing/SpendLimitForm";
import UsageDashboard from "@/pages/billing/UsageDashboard";
import {
  createPortalSession,
  getSubscriptionState,
} from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { useSessionGuardStore } from "@/store/useSessionGuardStore";
import { canManageBilling as canManageBillingFor } from "@/permissions/organizationPermissions";
import type { OrganizationRole } from "@/types/tenancy";
import { PageHeader } from "@/components/ui/PageHeader";


export const BillingHub: React.FC = () => {
  const { organization, organizationId, organizationRole } =
    useResolvedOrganization();

  // F-192. This page had its own rule (OWNER and BILLING "manage billing"), which matched
  // neither the server nor the permission model: BILLING was offered the payment portal, plan
  // switches and spend-limit edits the server refuses, and ADMIN could not set the spend limits
  // the server lets it set. Plan, payment method and seats: the owner. Spend limits: owner and
  // admin. Everything else on the page: every role that may open it, BILLING included.
  const role = String(organizationRole).toUpperCase() as OrganizationRole;
  const canManageBilling = canManageBillingFor(role);
  const canSetLimits = role === "OWNER" || role === "ADMIN";

  const { data: state, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: billingKeys.subscription(organizationId),
    queryFn: () => getSubscriptionState(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  // HARDENING-FINAL:billing-A. A stale session answers the portal request
  // with a step-up challenge. The client interceptor opens the password
  // modal but registers no replay, so the mutation failed, the red banner
  // showed alongside the modal, and confirming the password did nothing.
  // Now the challenge carries a replay of this request, the banner waits,
  // and a successful confirmation re-opens the portal by itself.
  const replayRef = useRef<() => void>(() => undefined);
  const [awaitingReauth, setAwaitingReauth] = useState(false);
  const stepUpPending = useSessionGuardStore((state) => state.stepUp !== null);
  useEffect(() => {
    if (!stepUpPending) {
      setAwaitingReauth(false);
    }
  }, [stepUpPending]);
  const portal = useMutation({
    mutationFn: () =>
      createPortalSession(organizationId, { return_url: window.location.href }),
    onSuccess: (session) => {
      window.location.assign(session.url);
    },
    onError: () => {
      const guard = useSessionGuardStore.getState();
      if (guard.stepUp !== null) {
        setAwaitingReauth(true);
        useSessionGuardStore.setState({
          stepUp: { ...guard.stepUp, retry: () => replayRef.current() },
        });
      }
    },
  });
  replayRef.current = () => {
    setAwaitingReauth(false);
    portal.mutate();
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {/* The dunning banner is rendered by the organization layout (ARCH-30 Tranche 3). */}

      <div className="mx-auto max-w-5xl space-y-6">
        <PageHeader
          icon={CreditCard}
          eyebrow={organization.organization_name}
          title="Billing"
          description="Your plan, seats, usage, spend limits and invoices."
          actions={
            canManageBilling && state?.has_billing_account ? (
              <button
                type="button"
                onClick={() => portal.mutate()}
                disabled={portal.isPending}
                className="fp-btn fp-btn-secondary"
              >
                {portal.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ExternalLink className="h-3.5 w-3.5" />
                )}
                Manage payment method
              </button>
            ) : null
          }
        />

        {portal.isError && !awaitingReauth && !stepUpPending && (
          <p role="alert" className="text-sm text-destructive">
            The billing portal couldn&apos;t be opened. If you were asked to
            confirm your password, try again.
          </p>
        )}

        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading billing…
          </div>
        ) : isError && !state ? (
          <div role="alert" className="rounded-lg border border-border bg-card p-4">
            <p className="text-sm font-medium text-foreground">
              Your subscription couldn&apos;t be loaded.
            </p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Nothing has changed with your plan. Try again in a moment.
            </p>
            <button
              type="button"
              onClick={() => void refetch()}
              disabled={isFetching}
              className="fp-btn fp-btn-secondary mt-3"
            >
              {isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              Try again
            </button>
          </div>
        ) : (
          <>
            {/* Primary view when there is no subscription */}
            {!state?.subscription && (
              <PlanSelector
                organizationId={organizationId}
                organizationSlug={organization.organization_slug}
                canManageBilling={canManageBilling}
                hasSubscription={false}
                currentSeats={state?.seats_purchased ?? 1}
                minimumSeats={state?.seats_used ?? 1}
              />
            )}

            <UsageDashboard organizationId={organizationId} />

            <ConsumptionDashboard organizationId={organizationId} />

            <SpendLimitForm organizationId={organizationId} canSetLimits={canSetLimits} />

            <SeatManager
              organizationId={organizationId}
              canManageBilling={canManageBilling}
            />

            <InvoicedContractPanel organizationId={organizationId} />

            <InvoiceBrowser organizationId={organizationId} />

            {/* Secondary view to allow upgrading / changing plan */}
            {state?.subscription && (
              <PlanSelector
                organizationId={organizationId}
                organizationSlug={organization.organization_slug}
                canManageBilling={canManageBilling}
                hasSubscription
                currentSeats={state.seats_purchased}
                minimumSeats={state.seats_used}
              />
            )}
          </>
        )}

        {!canManageBilling && (
          <p className="border-t border-border pt-4 text-xs text-muted-foreground">
            You can see the plan, usage, limits and invoices. Changing the plan or
            payment method needs an organization owner; owners, admins and billing
            managers can add or remove seats.
          </p>
        )}
      </div>
    </div>
  );
};

export default BillingHub;
