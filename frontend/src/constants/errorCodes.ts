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
 *
 * FE-0 completes the table. The pre-FE-0 file covered tenancy and invitations
 * only, which was everything ARCH-01 through ARCH-05 could raise. ARCH-10's
 * spend controls, ARCH-08's limiter, and SEC-1's re-authentication all emit
 * codes the client must now branch on, and a code that is absent here is a
 * code some call site is matching as a bare string.
 *
 * NOT EVERY BACKEND REFUSAL IS IN THIS TABLE
 * ==========================================
 *
 * Several routes raise `HTTPException` directly rather than a `FlowPilotError`
 * subclass, which produces `{"detail": "..."}` with no code at all —
 * `assistant_stream.py`'s 402 and every 403 in `billing.py` among them.
 * `parseErrorEnvelope` assigns those a fallback code, so `code` is `string |
 * undefined` at every call site and must be treated as optional.
 */

export const API_ERROR_CODES = {
  /* --- Transport, produced client-side ---------------------------------- */
  NETWORK_ERROR: "NETWORK_ERROR",
  UNAUTHORIZED: "UNAUTHORIZED",
  VALIDATION_ERROR: "VALIDATION_ERROR",
  SERVER_ERROR: "SERVER_ERROR",

  /* --- Generic ---------------------------------------------------------- */
  BAD_REQUEST: "BAD_REQUEST",

  /* --- Rate limiting (ARCH-08) ------------------------------------------ */
  RATE_LIMIT_EXCEEDED: "RATE_LIMIT_EXCEEDED",

  /* --- Spend controls (ARCH-10 Step 3) ---------------------------------- */
  SPEND_LIMIT_EXCEEDED: "SPEND_LIMIT_EXCEEDED",
  SPEND_LIMIT_MISCONFIGURED: "SPEND_LIMIT_MISCONFIGURED",
  SPEND_CONTROL_ERROR: "SPEND_CONTROL_ERROR",

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
  INVITATION_GRANT_INVALID: "INVITATION_GRANT_INVALID",
  INVITATION_RESEND_TOO_SOON: "INVITATION_RESEND_TOO_SOON",
  SEAT_LIMIT_EXCEEDED: "SEAT_LIMIT_EXCEEDED",

  /* --- Users ------------------------------------------------------------ */
  EMAIL_IMMUTABLE: "EMAIL_IMMUTABLE",
  REAUTHENTICATION_FAILED: "REAUTHENTICATION_FAILED",
  USER_ERROR: "USER_ERROR",

  /* --- Ownership transfer (ARCH-05 Step 6) ------------------------------ */
  PENDING_TRANSFER_EXISTS: "PENDING_TRANSFER_EXISTS",
  TRANSFER_NOT_FOUND: "TRANSFER_NOT_FOUND",
  TRANSFER_NOT_PENDING: "TRANSFER_NOT_PENDING",
  TRANSFER_EXPIRED: "TRANSFER_EXPIRED",
  TRANSFER_TARGET_MISMATCH: "TRANSFER_TARGET_MISMATCH",
  TRANSFER_INITIATOR_MISMATCH: "TRANSFER_INITIATOR_MISMATCH",
  TARGET_NOT_VERIFIED: "TARGET_NOT_VERIFIED",
  CANNOT_TRANSFER_TO_SELF: "CANNOT_TRANSFER_TO_SELF",
  OWNERSHIP_TRANSFER_ERROR: "OWNERSHIP_TRANSFER_ERROR",

  /* --- Streaming (ARCH-12 SSE error frames) ----------------------------- */
  GENERATION_FAILED: "GENERATION_FAILED",
} as const;

export type ApiErrorCode =
  (typeof API_ERROR_CODES)[keyof typeof API_ERROR_CODES];

/**
 * Codes that mean "sign in again". The session is gone, not merely
 * insufficient.
 *
 * The guards route these to /login with the destination preserved. This is the
 * set that must NEVER route to onboarding — conflating it with "no tenant" is
 * the exact defect ARCH-01 fixes.
 *
 * REAUTHENTICATION_FAILED is deliberately absent. It arrives on a 401 and it
 * looks like a session failure, but the session is fine — it is `auth_time`
 * that is stale. Signing the user out and back in would satisfy it by
 * accident, at the cost of destroying their session for a step-up that a modal
 * could have handled in place.
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

/**
 * Codes meaning a commercial ceiling was reached.
 *
 * These are held by `useSessionGuardStore` and surfaced as a persistent banner
 * rather than a toast, because they do not resolve on their own and a toast
 * that vanishes leaves the user with a broken feature and no explanation.
 *
 * Never retried. See the 402 branch in `client.ts`.
 */
export const QUOTA_EXHAUSTED_CODES: ReadonlySet<string> = new Set<string>([
  API_ERROR_CODES.SPEND_LIMIT_EXCEEDED,
]);

/**
 * Codes meaning fresh credentials are required for this specific action.
 *
 * Satisfied by a step-up modal, not by a refresh: `session_service` carries
 * `authenticated_at` forward across `/auth/refresh` — that is the whole point
 * of SEC-1's `auth_time` claim — so only a new session restamps it.
 */
export const STEP_UP_CODES: ReadonlySet<string> = new Set<string>([
  API_ERROR_CODES.REAUTHENTICATION_FAILED,
]);
