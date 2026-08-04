export const ROUTES = {
  LOGIN: "/login",
  REGISTER: "/register",

  DASHBOARD: "/",
  WORK_ITEMS: "/work-items",
  WORK_ITEM_DETAILS: "/work-items/:id",

  ASSISTANT: "/assistant",
  AUTOMATION: "/automation",

  NOTIFICATIONS: "/notifications",

  PROFILE: "/profile",

  SETTINGS: "/settings",
  ACCOUNT: "/account",

  INVITATION_ACCEPT: "/invitations/accept",

  NOT_FOUND: "*",
} as const;

export type RouteValue =
  (typeof ROUTES)[keyof typeof ROUTES];
