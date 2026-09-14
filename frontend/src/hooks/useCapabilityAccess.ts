import { useQuery } from "@tanstack/react-query";

import { getOrganizationEntitlements } from "@/services/api/entitlements";
import { entitlementKeys } from "@/services/api/queryKeys";

export interface CapabilityAccessResult {
  readonly granted: boolean;
  readonly isLoading: boolean;
  readonly isError: boolean;
}

/**
 * ARCH-31 Step 4 — whether the organization's tier bundles a capability.
 *
 * Distinct from `useAddonAccess` because a capability and an add-on have
 * different remedies. An add-on can be bought on the plan the customer
 * already has, so its lock card renders a checkout button. A capability is
 * bundled into a tier, so the only route to it is a plan change — and a
 * button that opened a purchase flow would send the reader somewhere with
 * nothing to sell them.
 *
 * Shares the entitlements query key with `useAddonAccess`, so a page that
 * renders both lock cards issues one request rather than two.
 */
export const useCapabilityAccess = (
  organizationId: string,
  capabilityKey: string,
): CapabilityAccessResult => {
  const query = useQuery({
    queryKey: entitlementKeys.all(organizationId),
    queryFn: () => getOrganizationEntitlements(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const capabilities = (query.data as { capabilities?: readonly string[] } | undefined)
    ?.capabilities;

  return {
    granted: Boolean(capabilities?.includes(capabilityKey)),
    isLoading: query.isLoading,
    isError: query.isError,
  };
};
