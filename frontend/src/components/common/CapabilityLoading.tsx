/**
 * F-164 — what a plan-gated page shows while it asks whether the plan includes it.
 *
 * Pages rendered their "included on the Business and Enterprise plans" lock while the
 * entitlements request was in flight, so every paying customer saw an upgrade message flash
 * before the page they pay for. The answer is unknown until the request returns: show the
 * page's outline, not a verdict.
 */
import React from "react";

export const CapabilityLoading: React.FC = () => (
  <div role="status" aria-live="polite" aria-label="Loading" className="space-y-4 p-4" data-testid="capability-loading">
    <div className="h-6 w-48 animate-pulse rounded-md bg-muted/70" />
    <div className="h-4 w-full max-w-xl animate-pulse rounded-md bg-muted/50" />
    <div className="grid gap-3 sm:grid-cols-3">
      <div className="h-20 animate-pulse rounded-xl bg-muted/40" />
      <div className="h-20 animate-pulse rounded-xl bg-muted/40" />
      <div className="h-20 animate-pulse rounded-xl bg-muted/40" />
    </div>
    <div className="h-64 animate-pulse rounded-xl bg-muted/30" />
  </div>
);

export default CapabilityLoading;
