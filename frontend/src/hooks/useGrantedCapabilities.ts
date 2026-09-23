import { useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { getOrganizationEntitlements } from "@/services/api/entitlements";
import { entitlementKeys } from "@/services/api/queryKeys";

export interface GrantedCapabilities {
  readonly granted: ReadonlySet<string>;
  /** HM-S1: the published plans that include a capability, cheapest first. */
  readonly plansFor: (capability: string) => readonly string[];
  readonly isLoading: boolean;
  readonly isError: boolean;
}

const NO_PLANS: readonly string[] = [];

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

  const data = query.data as
    | {
        capabilities?: readonly string[];
        capability_plans?: Readonly<Record<string, readonly string[]>>;
      }
    | undefined;
  const list = data?.capabilities;
  const plans = data?.capability_plans;

  const granted = useMemo(() => new Set<string>(list ?? []), [list]);
  const plansFor = useCallback(
    (capability: string): readonly string[] => plans?.[capability] ?? NO_PLANS,
    [plans],
  );

  return {
    granted,
    plansFor,
    isLoading: query.isLoading,
    isError: query.isError,
  };
};
