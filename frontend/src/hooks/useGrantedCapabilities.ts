import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { getOrganizationEntitlements } from "@/services/api/entitlements";
import { entitlementKeys } from "@/services/api/queryKeys";

export interface GrantedCapabilities {
  readonly granted: ReadonlySet<string>;
  readonly isLoading: boolean;
  readonly isError: boolean;
}

/**
 * ARCH36-S1:granted-capabilities — every capability the organization's tier
 * grants, as one set.
 *
 * `useCapabilityAccess` answers for one key, which suits a page. The sidebar
 * and the command palette need an answer for every gated entry at once, and a
 * hook per entry would put hook calls inside a loop over data.
 *
 * Same query key and the same fetcher as `useCapabilityAccess`, so a page and
 * the sidebar rendered together issue one request and cannot disagree.
 *
 * `GET /organizations/{id}/entitlements` is `RequireAnyOrgRole`, so a viewer
 * gets a real answer rather than a 403 that would lock every entry.
 */
export const useGrantedCapabilities = (
  organizationId: string,
): GrantedCapabilities => {
  const query = useQuery({
    queryKey: entitlementKeys.all(organizationId),
    queryFn: () => getOrganizationEntitlements(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const list = (query.data as { capabilities?: readonly string[] } | undefined)
    ?.capabilities;

  const granted = useMemo(() => new Set<string>(list ?? []), [list]);

  return {
    granted,
    isLoading: query.isLoading,
    isError: query.isError,
  };
};
