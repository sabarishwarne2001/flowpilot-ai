import React from "react";
import { AlertOctagon, ExternalLink, Loader2 } from "lucide-react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { createPortalSession, getBillingAccess } from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";

const HEALTHY_STATES = new Set(["ACTIVE", "OK", "CURRENT", "TRIALING"]);

export interface DunningBannerProps {
  readonly organizationId: string;
  readonly canManageBilling?: boolean;
  readonly className?: string;
}

export const DunningBanner: React.FC<DunningBannerProps> = ({
  organizationId,
  canManageBilling = false,
  className = "",
}) => {
  const { data: access } = useQuery({
    queryKey: billingKeys.access(organizationId),
    queryFn: () => getBillingAccess(organizationId),
    enabled: Boolean(organizationId),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });

  const portal = useMutation({
    mutationFn: () =>
      createPortalSession(organizationId, {
        return_url: window.location.href,
      }),
    onSuccess: (session) => {
      window.location.assign(session.url);
    },
  });

  if (!access || HEALTHY_STATES.has(access.access_state.toUpperCase())) {
    return null;
  }

  const stage = access.dunning_steps_applied.length;

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={`border-b border-destructive/40 bg-destructive/10 px-4 py-3 ${className}`}
    >
      <div className="mx-auto flex max-w-6xl items-start gap-3">
        <AlertOctagon
          className="mt-0.5 h-5 w-5 shrink-0 text-destructive"
          aria-hidden="true"
        />

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-destructive">
            {access.writes_allowed
              ? "There's a problem with your payment method"
              : "Your account is read-only until payment is resolved"}
          </p>

          <p className="mt-1 text-sm text-foreground/80">
            {access.writes_allowed ? (
              <>
                A payment attempt failed. Update your billing details to avoid
                interruption.
              </>
            ) : (
              <>
                You can still read, search, and export everything. New uploads,
                AI generation, and edits are paused.
              </>
            )}
          </p>

          {(access.export_allowed || access.data_retained) && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              Your data is safe.
              {access.data_retained && " Nothing has been deleted."}
              {access.export_allowed && " Export remains available."}
            </p>
          )}

          {stage > 0 && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              {stage} {stage === 1 ? "reminder has" : "reminders have"} been
              sent
              {access.next_dunning_step
                ? `. Next step: ${access.next_dunning_step.toLowerCase().replace(/_/g, " ")}.`
                : "."}
            </p>
          )}

          <div className="mt-2.5">
            {canManageBilling ? (
              <button
                type="button"
                onClick={() => portal.mutate()}
                disabled={portal.isPending}
                className="inline-flex items-center gap-1.5 rounded-md bg-destructive px-3 py-1.5 text-xs font-medium text-destructive-foreground hover:opacity-90 disabled:opacity-60"
              >
                {portal.isPending ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Opening…
                  </>
                ) : (
                  <>
                    Update payment method
                    <ExternalLink className="h-3.5 w-3.5" />
                  </>
                )}
              </button>
            ) : (
              <p className="text-xs text-muted-foreground">
                Ask an organization owner or billing administrator to update
                the payment method.
              </p>
            )}

            {portal.isError && (
              <p className="mt-1.5 text-xs text-destructive">
                Couldn&apos;t open the billing portal. Try again in a moment.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default DunningBanner;
