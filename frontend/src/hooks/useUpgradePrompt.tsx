import React, { useCallback, useState } from "react";

import { useQuery } from "@tanstack/react-query";

import { UpgradePlanDialog } from "@/components/billing/UpgradePlanDialog";
import { useGrantedCapabilities } from "@/hooks/useGrantedCapabilities";
import { organizationBillingPath } from "@/routes/tenantPaths";
import { getOrganizationEntitlements } from "@/services/api/entitlements";
import { entitlementKeys } from "@/services/api/queryKeys";

interface PendingPrompt {
  readonly capability: string;
  readonly name: string;
}

export interface UpgradePrompt {
  /**
   * True only once entitlements have loaded and the capability (or, F-005,
   * the `addon.*` key) is absent.
   */
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
  // F-005. Same query (and key) as useGrantedCapabilities and useAddonAccess,
  // so this adds no request. An add-on is locked when it is not granted or has
  // lapsed; during a downgrade grace it still works and is not locked.
  const entitlements = useQuery({
    queryKey: entitlementKeys.all(organizationId),
    queryFn: () => getOrganizationEntitlements(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });
  const addonFor = useCallback(
    (key: string) => entitlements.data?.addons.find((addon) => addon.addon_key === key) ?? null,
    [entitlements.data],
  );
  const [pending, setPending] = useState<PendingPrompt | null>(null);
  const role = organizationRole.toUpperCase();

  const isLocked = useCallback(
    (capability: string | undefined): boolean => {
      if (capability === undefined || capabilities.isLoading || capabilities.isError) {
        return false;
      }
      if (capability.startsWith("addon.")) {
        const addon = addonFor(capability);
        return addon !== null && (addon.state === "NOT_GRANTED" || addon.state === "LAPSED");
      }
      return !capabilities.granted.has(capability);
    },
    [capabilities.isLoading, capabilities.isError, capabilities.granted, addonFor],
  );
  const prompt = useCallback((capability: string, name: string) => setPending({ capability, name }), []);

  const dialog = (
    <UpgradePlanDialog
      open={pending !== null}
      featureName={pending?.name ?? ""}
      includedIn={
        pending
          ? pending.capability.startsWith("addon.")
            ? addonFor(pending.capability)?.included_in ?? []
            : capabilities.plansFor(pending.capability)
          : []
      }
      canChangePlan={role === "OWNER" || role === "BILLING"}
      billingPath={organizationBillingPath(organizationSlug)}
      onClose={() => setPending(null)}
    />
  );

  return { isLocked, prompt, dialog };
}

export default useUpgradePrompt;
