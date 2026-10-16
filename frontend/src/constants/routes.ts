/**
 * Global route constants for Servabo.
 */

export const ROUTES = {
  LOGIN: "/login",
  /**
   * ARCH-30 Tranche 1 (T4-F4). Where federated logins land. Must equal
   * SSO_COMPLETE_PATH in backend/app/api/v1/saml.py (gate 30T1-G7).
   */
  SSO_COMPLETE: "/auth/sso/complete",
  REGISTER: "/register",
  VERIFY_EMAIL: "/verify-email",
  FORGOT_PASSWORD: "/forgot-password",
  RESET_PASSWORD: "/reset-password",
  ONBOARDING: "/onboarding",
  NEW_ORGANIZATION: "/organizations/new",
  WORKSPACES: "/workspaces",
  NO_ACCESS: "/no-access",
  INVITATION_ACCEPT: "/invitations/accept",
  // Campaign session 4: public pages, readable signed out and signed in.
  PRICING: "/pricing",
  TERMS: "/terms",
  PRIVACY: "/privacy",

  DASHBOARD: "/",
  WORK_ITEMS: "/work-items",
  WORK_ITEM_DETAILS: "/work-items/:id",
  ASSISTANT: "/assistant",
  AUTOMATION: "/automation",
  NOTIFICATIONS: "/notifications",
  PROFILE: "/profile",
  SETTINGS: "/settings",
  ACCOUNT: "/account",

  NOT_FOUND: "*",
} as const;

export type RouteValue = (typeof ROUTES)[keyof typeof ROUTES];

/** Campaign session 4. The public pages, as the footers of the public and sign-in pages link them. */
export const PUBLIC_PAGE_LINKS: ReadonlyArray<{ readonly to: RouteValue; readonly label: string }> = [
  { to: ROUTES.PRICING, label: "Pricing" },
  { to: ROUTES.TERMS, label: "Terms of service" },
  { to: ROUTES.PRIVACY, label: "Privacy policy" },
];
