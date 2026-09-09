/**
 * Global route constants for FlowPilot AI.
 */

export const ROUTES = {
  LOGIN: "/login",
  REGISTER: "/register",
  VERIFY_EMAIL: "/verify-email",
  FORGOT_PASSWORD: "/forgot-password",
  RESET_PASSWORD: "/reset-password",
  ONBOARDING: "/onboarding",
  NEW_ORGANIZATION: "/organizations/new",
  WORKSPACES: "/workspaces",
  NO_ACCESS: "/no-access",
  INVITATION_ACCEPT: "/invitations/accept",

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
