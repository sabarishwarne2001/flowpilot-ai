import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Info, Loader2, Users } from "lucide-react";

import { getSubscriptionState } from "@/services/api/billing";
import { listIdpConfigs } from "@/services/api/identity";
import { billingKeys, identityKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import type { IdpConfigRead, JitProvisioningMode } from "@/types/identity";

const MODE_COPY: Record<
  JitProvisioningMode,
  { label: string; summary: string; risk: "high" | "medium" | "low" }
> = {
  OPEN: {
    label: "Open",
    summary:
      "Anyone who signs in through the identity provider is added as an active member. No ceiling.",
    risk: "high",
  },
  CAPPED: {
    label: "Capped",
    summary:
      "New members are added automatically up to a seat limit, then refused.",
    risk: "medium",
  },
  INVITE_ONLY: {
    label: "Invite only",
    summary:
      "Signing in through the identity provider does not create an account. Members must be invited first.",
    risk: "low",
  },
};

export const JitPolicyPanel: React.FC = () => {
  const { organizationId } = useResolvedOrganization();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const configsQuery = useQuery({
    queryKey: identityKeys.idpConfigs(organizationId),
    queryFn: () => listIdpConfigs(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const subscriptionQuery = useQuery({
    queryKey: billingKeys.subscription(organizationId),
    queryFn: () => getSubscriptionState(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 30_000,
  });

  const configs = configsQuery.data ?? [];
  const active = useMemo<IdpConfigRead | null>(
    () => configs.find((c) => c.id === selectedId) ?? configs[0] ?? null,
    [configs, selectedId],
  );

  if (configsQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading policy…
      </div>
    );
  }

  if (!active) {
    return (
      <div className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium">Provisioning policy</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          No identity provider is configured. Set one up first — the
          provisioning policy is chosen when the connection is created.
        </p>
      </div>
    );
  }

  const mode = (active.jit_provisioning_mode as JitProvisioningMode) ?? "CAPPED";
  const copy = MODE_COPY[mode] ?? MODE_COPY.CAPPED;
  const cap = active.effective_seat_cap;
  const seats = active.current_billable_seats;
  const remaining = cap === null ? null : Math.max(cap - seats, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Provisioning policy</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            What happens when someone signs in through your identity provider
            for the first time.
          </p>
        </div>

        {configs.length > 1 && (
          <select
            value={active.id}
            onChange={(e) => setSelectedId(e.target.value)}
            aria-label="Identity provider"
            className="rounded-md border border-border bg-background px-2 py-1.5 text-xs"
          >
            {configs.map((config) => (
              <option key={config.id} value={config.id}>
                {config.display_name}
              </option>
            ))}
          </select>
        )}
      </div>

      <div
        className={[
          "rounded-lg border p-4",
          copy.risk === "high"
            ? "border-destructive/50 bg-destructive/5"
            : "border-border bg-card",
        ].join(" ")}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{copy.label}</span>
          {copy.risk === "high" && (
            <span className="inline-flex items-center gap-1 rounded bg-destructive/15 px-1.5 py-0.5 text-[11px] font-medium text-destructive">
              <AlertTriangle className="h-3 w-3" />
              Uncapped
            </span>
          )}
        </div>

        <p className="mt-1 text-sm text-muted-foreground">{copy.summary}</p>

        <div className="mt-3 rounded-md border border-border bg-background p-3">
          <p className="flex items-start gap-2 text-sm">
            <Info
              className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
            <span>
              <strong className="font-medium">
                Each person who signs in through the identity provider becomes
                an active member and adds a billable seat to your subscription,
                prorated immediately.
              </strong>{" "}
              {mode === "OPEN" ? (
                <>
                  There is no ceiling on this. Your identity provider controls
                  who signs in and when, so seat growth happens at their
                  timing — not yours.
                </>
              ) : mode === "CAPPED" ? (
                <>
                  Provisioning stops once the seat cap is reached, so growth is
                  bounded.
                </>
              ) : (
                <>
                  No seats are added automatically — members must be invited
                  first.
                </>
              )}
            </span>
          </p>

          <p className="mt-2 text-xs text-muted-foreground">
            Your per-seat rate is on your current invoice and in the billing
            portal.
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-center gap-2">
          <Users className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <h3 className="text-sm font-medium">Seats</h3>
        </div>

        <dl className="mt-3 grid grid-cols-3 gap-4">
          <div>
            <dt className="text-xs text-muted-foreground">Billable now</dt>
            <dd className="mt-0.5 text-2xl font-semibold tabular-nums">
              {seats}
            </dd>
          </div>

          <div>
            <dt className="text-xs text-muted-foreground">Cap in force</dt>
            <dd className="mt-0.5 text-2xl font-semibold tabular-nums">
              {cap ?? "—"}
            </dd>
            {cap !== null && active.jit_seat_cap !== null && cap < active.jit_seat_cap && (
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                Lower than the configured {active.jit_seat_cap} — your
                subscription caps it.
              </p>
            )}
          </div>

          <div>
            <dt className="text-xs text-muted-foreground">Remaining</dt>
            <dd
              className={[
                "mt-0.5 text-2xl font-semibold tabular-nums",
                remaining !== null && remaining === 0 ? "text-destructive" : "",
              ].join(" ")}
            >
              {remaining ?? "∞"}
            </dd>
          </div>
        </dl>

        {remaining === 0 && (
          <p role="alert" className="mt-3 text-xs font-medium text-destructive">
            The cap is reached. New sign-ins through the identity provider are
            being refused until the cap is raised or a member is removed.
          </p>
        )}

        {mode === "OPEN" && subscriptionQuery.data && (
          <p className="mt-3 flex items-start gap-1.5 text-xs text-amber-700">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Your subscription is currently billed for{" "}
            {subscriptionQuery.data.seats_purchased} seats and{" "}
            {subscriptionQuery.data.seats_billable} are in use. With an
            uncapped policy this gap can widen without any action here.
          </p>
        )}

        <p className="mt-3 border-t border-border pt-3 text-xs text-muted-foreground">
          Default role for new members:{" "}
          <span className="font-medium">{active.jit_default_org_role}</span>
        </p>
      </div>

      <p className="text-xs text-muted-foreground">
        The provisioning mode and seat cap are set when an identity provider
        connection is created. To change them, create a new connection and
        activate it.
      </p>
    </div>
  );
};

export default JitPolicyPanel;
