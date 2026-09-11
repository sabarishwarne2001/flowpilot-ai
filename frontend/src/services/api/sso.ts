import apiClient, { apiHref } from "@/services/api/client";
import { SSO_ENDPOINTS } from "@/services/api/endpoints";
import type { SsoDiscovery } from "@/types/sso";

/**
 * ARCH-30 Tranche 1 (T4-F4) — the SSO entry point on the login page.
 */

/**
 * The domain part of a work email, or null if there is not a plausible one.
 *
 * `lastIndexOf` because the local part of an address may legally contain `@`
 * when quoted, and the domain is always what follows the final one. The
 * backend normalises the same way (`rsplit("@", 1)`), so both sides agree on
 * which domain was asked about.
 */
export const emailDomain = (email: string): string | null => {
  const trimmed = email.trim();
  const at = trimmed.lastIndexOf("@");
  if (at < 1 || at === trimmed.length - 1) {
    return null;
  }
  const domain = trimmed.slice(at + 1).toLowerCase();
  return domain.includes(".") && domain.length >= 3 ? domain : null;
};

/**
 * Does this email domain sign in through an enterprise identity provider?
 *
 * Unauthenticated and answers for any domain, which makes it an oracle for
 * "which companies use FlowPilot with SSO". That is the accepted industry
 * trade (Slack, Notion and Okta's own dashboard all expose the same signal) and
 * the page only calls it when the user explicitly chooses single sign-on,
 * never on each keystroke.
 */
export const discoverSso = async (domain: string): Promise<SsoDiscovery> => {
  const response = await apiClient.get<SsoDiscovery>(SSO_ENDPOINTS.discover, {
    params: { domain },
  });
  return response.data;
};

/**
 * The URL that starts the IdP handshake, for `window.location.assign`.
 *
 * `redirect_path` must already have passed `isSafeRedirectPath`. The backend
 * re-checks it with `is_safe_redirect_path` before storing it on the auth
 * request, and the completion page checks it a third time before navigating;
 * three checks on one value is deliberate, because it crosses two trust
 * boundaries and an IdP round trip.
 */
export const ssoStartHref = (domain: string, redirectPath: string): string => {
  const query = new URLSearchParams({ domain, redirect_path: redirectPath });
  return apiHref(`${SSO_ENDPOINTS.start}?${query.toString()}`);
};
