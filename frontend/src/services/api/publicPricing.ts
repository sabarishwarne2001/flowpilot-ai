import apiClient from "@/services/api/client";
import type { PublicPricingResponse } from "@/types/publicPricing";

/**
 * Campaign session 4 — the public price list behind the /pricing page.
 *
 * No sign-in and no organization: the server answers the same thing to everyone
 * (registered in its public route registry), and may be cached for five minutes.
 * It is not under `/public/...`, which is the API-key developer gateway.
 */
export const PUBLIC_PRICING_ENDPOINT = "/pricing";

export const publicPricingKeys = {
  // Not organization-scoped: the request is not addressed by organization.
  list: ["public", "pricing"] as const,
};

export const getPublicPricing = async (): Promise<PublicPricingResponse> => {
  const response = await apiClient.get<PublicPricingResponse>(PUBLIC_PRICING_ENDPOINT, {
    headers: { Accept: "application/json" },
  });
  return response.data;
};
