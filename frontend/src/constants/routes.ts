/**
 * Centralized client-side route definitions for FlowPilot AI.
 *
 * All application routes should be referenced from this file.
 * This prevents hardcoded paths and provides compile-time safety.
 */

export const ROUTES = {
  // ============================
  // Public Routes
  // ============================

  LOGIN: "/login",
  REGISTER: "/register",

  // ============================
  // Protected Routes
  // ============================

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

/**
 * Union type representing all valid application routes.
 */
export type RouteValue =
  (typeof ROUTES)[keyof typeof ROUTES];
