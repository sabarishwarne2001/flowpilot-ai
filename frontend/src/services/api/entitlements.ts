import apiClient from "@/services/api/client";
import { ENTITLEMENT_ENDPOINTS } from "@/services/api/endpoints";
import { ApiError } from "@/services/api/errors";
import type { EphemeralSessionResponse } from "@/types/billing";
import type {
  AddonKey,
  AddonRequiredDetail,
  AddonState,
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

/**
 * The add-on refusal, if that is what this error is.
 *
 * ARCH-30 Tranche 3. The client rejects with `ApiError`, never with the raw
 * Axios error, so the Tranche 2 version — which read `error.response` — could
 * not match anything. The backend now sends the ARCH-01 envelope, which lands
 * here as `code` and `details`.
 */
export const addonRequiredDetail = (error: unknown): AddonRequiredDetail | null => {
  if (!(error instanceof ApiError) || error.status !== 402 || error.code !== "ADDON_REQUIRED") {
    return null;
  }
  const details = error.details;
  return {
    code: "ADDON_REQUIRED",
    addon_key: details.addon_key as AddonKey,
    addon_name: typeof details.addon_name === "string" ? details.addon_name : "",
    state: (details.state as AddonState | undefined) ?? "NOT_GRANTED",
    grace_ends_at: typeof details.grace_ends_at === "string" ? details.grace_ends_at : null,
    message: error.message,
  };
};
