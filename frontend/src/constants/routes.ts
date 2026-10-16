/**
 * Global route constants for Servabo.
 */
import { WEBSITE } from "@/config/website";

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
  // Campaign session 4: public pages. Website session (B7): they redirect to the website.
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

/**
 * Campaign session 4. The public pages, as the sign-in and sign-up pages' footers link them.
 * Website session (B7): on the public website, so these are full addresses.
 */
export const PUBLIC_PAGE_LINKS: ReadonlyArray<{ readonly href: string; readonly label: string }> = [
  { href: WEBSITE.pricing, label: "Pricing" },
  { href: WEBSITE.terms, label: "Terms of service" },
  { href: WEBSITE.privacy, label: "Privacy policy" },
];
