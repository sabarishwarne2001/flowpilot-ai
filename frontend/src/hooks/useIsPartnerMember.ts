import { useQuery } from "@tanstack/react-query";

import { partnerApi } from "@/services/api/partner";
import { partnerKeys } from "@/services/api/queryKeys";

/**
 * ARCH36-S1:partner-membership — whether the signed-in user belongs to any
 * reseller partner (ARCH-27).
 *
 * `GET /partners` answers for the caller only and returns an empty list for a
 * user with no partner membership, so the sidebar can ask it of everyone.
 * It shares `partnerKeys.mine()` with PartnerPortal, so opening the portal
 * after the sidebar has asked costs no second request.
 *
 * Hiding the link is not what protects the portal: every `/partners/{id}/...`
 * endpoint authenticates a partner principal on the server.
 */
export const useIsPartnerMember = (): boolean => {
  const query = useQuery({
    queryKey: partnerKeys.mine(),
    queryFn: partnerApi.listMine,
    staleTime: 5 * 60_000,
    retry: false,
  });

  return (query.data?.length ?? 0) > 0;
};
