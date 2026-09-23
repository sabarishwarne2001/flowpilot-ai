import React, { useCallback, useState } from "react";

import { UpgradePlanDialog } from "@/components/billing/UpgradePlanDialog";
import { useGrantedCapabilities } from "@/hooks/useGrantedCapabilities";
import { organizationBillingPath } from "@/routes/tenantPaths";

interface PendingPrompt {
  readonly capability: string;
  readonly name: string;
}

export interface UpgradePrompt {
  /** True only once entitlements have loaded and the capability is absent. */
  readonly isLocked: (capability: string | undefined) => boolean;
  readonly prompt: (capability: string, name: string) => void;
  readonly dialog: React.ReactElement;
}

/**
 * HM-S1:upgrade-prompt — lock state and the upgrade dialog for a navigation
 * surface. Nothing is ever shown as locked while entitlements load, so an
 * entitled tenant never sees a lock flash on and off.
 */
export function useUpgradePrompt(
  organizationId: string,
  organizationSlug: string,
  organizationRole: string,
): UpgradePrompt {
  const capabilities = useGrantedCapabilities(organizationId);
  const [pending, setPending] = useState<PendingPrompt | null>(null);
  const role = organizationRole.toUpperCase();

  const isLocked = useCallback(
    (capability: string | undefined): boolean =>
      capability !== undefined && !capabilities.isLoading && !capabilities.isError && !capabilities.granted.has(capability),
    [capabilities.isLoading, capabilities.isError, capabilities.granted],
  );
  const prompt = useCallback((capability: string, name: string) => setPending({ capability, name }), []);

  const dialog = (
    <UpgradePlanDialog
      open={pending !== null}
      featureName={pending?.name ?? ""}
      includedIn={pending ? capabilities.plansFor(pending.capability) : []}
      canChangePlan={role === "OWNER" || role === "BILLING"}
      billingPath={organizationBillingPath(organizationSlug)}
      onClose={() => setPending(null)}
    />
  );

  return { isLocked, prompt, dialog };
}

export default useUpgradePrompt;
