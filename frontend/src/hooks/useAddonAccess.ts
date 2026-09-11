import { useQuery } from "@tanstack/react-query";

import { getOrganizationEntitlements } from "@/services/api/entitlements";
import { entitlementKeys } from "@/services/api/queryKeys";
import type { AddonAccess, AddonKey } from "@/types/entitlements";

export interface AddonAccessResult {
  readonly access: AddonAccess | null;
  readonly isLoading: boolean;
  readonly isError: boolean;
}

/**
 * ARCH-30 Tranche 2 (D-8). One request per organization, shared by every page
 * that renders a lock card; each caller selects its own add-on from it.
 */
export const useAddonAccess = (
  organizationId: string,
  addonKey: AddonKey,
): AddonAccessResult => {
  const query = useQuery({
    queryKey: entitlementKeys.all(organizationId),
    queryFn: () => getOrganizationEntitlements(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
    refetchOnWindowFocus: true,
  });

  return {
    access: query.data?.addons.find((addon) => addon.addon_key === addonKey) ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
  };
};
