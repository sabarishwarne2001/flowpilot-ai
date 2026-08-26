import React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ExternalLink, Loader2 } from "lucide-react";

import DunningBanner from "@/components/billing/DunningBanner";
import InvoiceBrowser from "@/pages/billing/InvoiceBrowser";
import SeatManager from "@/pages/billing/SeatManager";
import UsageDashboard from "@/pages/billing/UsageDashboard";
import {
  createPortalSession,
  getSubscriptionState,
} from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";

const BILLING_ROLES = new Set(["OWNER", "BILLING"]);

export const BillingHub: React.FC = () => {
  const { organization, organizationId, organizationRole } =
    useResolvedOrganization();

  const canManageBilling = BILLING_ROLES.has(
    String(organizationRole).toUpperCase(),
  );

  const { data: state, isLoading } = useQuery({
    queryKey: billingKeys.subscription(organizationId),
    queryFn: () => getSubscriptionState(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const portal = useMutation({
    mutationFn: () =>
      createPortalSession(organizationId, { return_url: window.location.href }),
    onSuccess: (session) => {
      window.location.assign(session.url);
    },
  });

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <DunningBanner
        organizationId={organizationId}
        canManageBilling={canManageBilling}
      />

      <div className="mx-auto max-w-4xl space-y-6 p-4 sm:p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">Billing</h1>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {organization.organization_name}
            </p>
          </div>

          {canManageBilling && state?.has_billing_account && (
            <button
              type="button"
              onClick={() => portal.mutate()}
              disabled={portal.isPending}
              className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-60"
            >
              {portal.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <ExternalLink className="h-3.5 w-3.5" />
              )}
              Manage payment method
            </button>
          )}
        </header>

        {portal.isError && (
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
        ) : (
          <>
            <UsageDashboard organizationId={organizationId} />

            <SeatManager
              organizationId={organizationId}
              canManageBilling={canManageBilling}
            />

            <InvoiceBrowser organizationId={organizationId} />
          </>
        )}

        {!canManageBilling && (
          <p className="border-t border-border pt-4 text-xs text-muted-foreground">
            You can see usage and invoices. Changing the plan, seats, or payment
            method requires an organization owner or billing administrator.
          </p>
        )}
      </div>
    </div>
  );
};

export default BillingHub;
