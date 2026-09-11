import apiClient from "@/services/api/client";
import { ENTITLEMENT_ENDPOINTS } from "@/services/api/endpoints";
import type { EphemeralSessionResponse } from "@/types/billing";
import type {
  AddonKey,
  AddonRequiredDetail,
  OrganizationEntitlements,
} from "@/types/entitlements";

export const getOrganizationEntitlements = async (
  organizationId: string,
): Promise<OrganizationEntitlements> => {
  const response = await apiClient.get<OrganizationEntitlements>(
    ENTITLEMENT_ENDPOINTS.entitlements(organizationId),
  );
  return response.data;
};

/**
 * Start a checkout for one add-on. The success and cancel URLs are the
 * deployment's own; the server does not accept them from the browser.
 */
export const createAddonCheckoutSession = async (
  organizationId: string,
  addonKey: AddonKey,
): Promise<EphemeralSessionResponse> => {
  const response = await apiClient.post<EphemeralSessionResponse>(
    ENTITLEMENT_ENDPOINTS.addonCheckout(organizationId, addonKey),
  );
  return response.data;
};

/** Narrow an unknown error to the add-on 402 body, if that is what it is. */
export const addonRequiredDetail = (error: unknown): AddonRequiredDetail | null => {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response;
  if (response?.status !== 402) {
    return null;
  }
  const detail = response.data?.detail as Partial<AddonRequiredDetail> | undefined;
  return detail && detail.code === "ADDON_REQUIRED" ? (detail as AddonRequiredDetail) : null;
};
