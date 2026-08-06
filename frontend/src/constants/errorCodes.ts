/**
 * Stable API error codes emitted by the FlowPilot AI backend.
 *
 * Mirrors the _EXCEPTION_MAPPING table in app/core/exception_handlers.py.
 * These codes are the contract; the accompanying message is human-facing prose
 * and may be reworded without notice. Branch on the code, display the message.
 *
 * Two mappings deserve attention because they look surprising:
 *
 *   RESOURCE_NOT_FOUND at 404 covers both "does not exist" and "you are not a
 *   member". That collapse is deliberate on the backend: returning 403 for a
 *   tenant the caller cannot access would confirm the tenant exists, which is
 *   an enumeration oracle. GitHub applies the same rule to private
 *   repositories. The frontend therefore cannot distinguish the two either,
 *   and should not try.
 *
 *   TENANT_SUSPENDED at 403 is distinct from PERMISSION_DENIED so the client
 *   can render an explanatory tombstone ("this workspace is archived") rather
 *   than a generic "you don't have permission".
 */

export const API_ERROR_CODES = {
  /* --- Transport, produced client-side ---------------------------------- */
  NETWORK_ERROR: "NETWORK_ERROR",
  UNAUTHORIZED: "UNAUTHORIZED",
  VALIDATION_ERROR: "VALIDATION_ERROR",
  SERVER_ERROR: "SERVER_ERROR",

  /* --- Generic ---------------------------------------------------------- */
  BAD_REQUEST: "BAD_REQUEST",

  /* --- Tenancy ---------------------------------------------------------- */
  RESOURCE_NOT_FOUND: "RESOURCE_NOT_FOUND",
  PERMISSION_DENIED: "PERMISSION_DENIED",
  TENANT_SUSPENDED: "TENANT_SUSPENDED",

  /* --- Organization ----------------------------------------------------- */
  ORGANIZATION_ERROR: "ORGANIZATION_ERROR",
  ORGANIZATION_ALREADY_EXISTS: "ORGANIZATION_ALREADY_EXISTS",
  ORGANIZATION_MEMBER_ERROR: "ORGANIZATION_MEMBER_ERROR",
  LAST_OWNER: "LAST_OWNER",

  /* --- Workspace -------------------------------------------------------- */
  WORKSPACE_ERROR: "WORKSPACE_ERROR",
  WORKSPACE_ALREADY_EXISTS: "WORKSPACE_ALREADY_EXISTS",
  WORKSPACE_MEMBER_ERROR: "WORKSPACE_MEMBER_ERROR",

  /* --- Slugs ------------------------------------------------------------ */
  SLUG_ERROR: "SLUG_ERROR",
  INVALID_SLUG: "INVALID_SLUG",
  SLUG_RESERVED: "SLUG_RESERVED",
  SLUG_UNAVAILABLE: "SLUG_UNAVAILABLE",

  /* --- Invitations ------------------------------------------------------ */
  INVITATION_ERROR: "INVITATION_ERROR",
  INVITATION_EXPIRED: "INVITATION_EXPIRED",
  INVITATION_ALREADY_PROCESSED: "INVITATION_ALREADY_PROCESSED",
  INVITATION_ALREADY_MEMBER: "INVITATION_ALREADY_MEMBER",
  INVITATION_ALREADY_EXISTS: "INVITATION_ALREADY_EXISTS",
  INVALID_INVITATION_TOKEN: "INVALID_INVITATION_TOKEN",
  INVITATION_EMAIL_MISMATCH: "INVITATION_EMAIL_MISMATCH",
} as const;

export type ApiErrorCode =
  (typeof API_ERROR_CODES)[keyof typeof API_ERROR_CODES];

/**
 * Codes that mean "sign in again". The session is gone, not merely
 * insufficient.
 *
 * The guards in Step 6 route these to /login with the destination preserved.
 * This is the set that must NEVER route to onboarding — conflating it with
 * "no tenant" is the exact defect ARCH-01 fixes.
 */
export const AUTH_FAILURE_CODES: ReadonlySet<string> = new Set<string>([
  API_ERROR_CODES.UNAUTHORIZED,
]);

/**
 * Codes meaning the tenant exists but is not currently usable.
 *
 * Render a tombstone explaining what happened, not a permission error.
 */
export const TENANT_UNAVAILABLE_CODES: ReadonlySet<string> = new Set<string>([
  API_ERROR_CODES.TENANT_SUSPENDED,
]);

/**
 * Codes meaning the tenant is unreachable for this actor.
 *
 * Indistinguishable from "does not exist" by design; see the module docstring.
 * Both resolve to the workspace picker, or to onboarding when the actor
 * belongs to nothing.
 */
export const TENANT_UNREACHABLE_CODES: ReadonlySet<string> = new Set<string>([
  API_ERROR_CODES.RESOURCE_NOT_FOUND,
]);
